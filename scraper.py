from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from client import TunisieIndustrieClient
from config import DEFAULT_OUTPUT_PATH, DEFAULT_SEARCH_SECTOR, MAX_PHASE1_LIMIT, ScraperConfig
from crm.simple import (
    SimpleExportError,
    export_simple_records,
    load_simple_template_schema,
    verify_simple_workbook,
)
from discovery import DiscoveryRun, discover_to_checkpoint
from exporter import export_records, verify_workbook
from models import CompanyCandidate, CompanyRecord, ScrapeStats
from normalizer import apply_verified_share_capital, normalize_record
from parser import decode_html, parse_company_detail
from state import CheckpointStore, StateError


LOGGER = logging.getLogger("tunisie_industrie_scraper")


def export_simple_if_requested(records: list[CompanyRecord], args: argparse.Namespace) -> tuple[dict[str, object], dict[str, object]] | None:
    if args.simple_template is None:
        return None
    try:
        report = export_simple_records(records, args.simple_template, args.simple_output)
        schema = load_simple_template_schema(args.simple_template)
        summary = verify_simple_workbook(args.simple_output, schema, len(records))
    except (SimpleExportError, AssertionError) as exc:
        LOGGER.error("SIMPLE export failed: %s", exc)
        raise
    return report.as_dict(), summary


def fetch_company_record(client: TunisieIndustrieClient, candidate: CompanyCandidate, scraped_at: str) -> CompanyRecord:
    response = client.request("GET", candidate.detail_url)
    html = decode_html(response.content, response.headers.get("Content-Type"))
    record = parse_company_detail(html, response.url, candidate.source_id)
    if record.share_capital_dt and "?" in record.share_capital_dt and candidate.source_id:
        verified_capital = client.fetch_same_record_french_share_capital(candidate.source_id)
        corrected = apply_verified_share_capital(record, verified_capital)
        if corrected.share_capital_dt != record.share_capital_dt:
            LOGGER.info(
                "Corrected Share Capital DT for Source ID %s from the same record's French representation",
                candidate.source_id,
            )
        record = corrected
    record.scraped_at = scraped_at
    return normalize_record(record)


def scrape_companies(client: TunisieIndustrieClient, limit: int, sector: str) -> tuple[list[CompanyRecord], ScrapeStats]:
    """Preserve the Phase 1 bounded single-sector behavior."""

    stats = ScrapeStats(requested_limit=limit)
    criteria = {"secteur": sector, "action": "search"}
    candidates = client.discover(limit, criteria)
    stats.discovered = len(candidates)
    records: list[CompanyRecord] = []
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    for candidate in candidates:
        stats.fetched += 1
        try:
            records.append(fetch_company_record(client, candidate, scraped_at))
        except Exception as exc:  # noqa: BLE001 - one malformed public record should not abort the sample.
            stats.failed += 1
            stats.failures.append(candidate.detail_url)
            LOGGER.exception("Failed to parse %s: %s", candidate.detail_url, exc)
    stats.successfully_parsed = len(records)
    return records, stats


def _phase2_candidate_keys(store: CheckpointStore, discovery: DiscoveryRun, limit: int) -> list[str]:
    selected: list[str] = []
    selected_set: set[str] = set()
    for scope in discovery.scopes:
        count = 0
        for key in store.candidate_keys_for_scopes([scope.code]):
            if key in selected_set:
                continue
            selected.append(key)
            selected_set.add(key)
            count += 1
            if count >= limit:
                break
    return selected


def scrape_checkpointed(
    client: TunisieIndustrieClient,
    store: CheckpointStore,
    candidate_keys: list[str],
) -> dict[str, int]:
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    fetched = 0
    scraped = 0
    skipped = 0
    failed_before = len(store.failures)
    for key in candidate_keys:
        if store.has_record(key):
            skipped += 1
            continue
        candidate = store.candidate(key)
        fetched += 1
        try:
            record = fetch_company_record(client, candidate, scraped_at)
            if store.save_record(record):
                scraped += 1
        except Exception as exc:  # noqa: BLE001 - record the failure and continue.
            store.record_failure(candidate, exc)
            LOGGER.exception("Failed Source ID %s at %s", candidate.source_id, candidate.detail_url)
    return {
        "fetched": fetched,
        "scraped": scraped,
        "skipped_completed": skipped,
        "failures_before": failed_before,
        "failures_after": len(store.failures),
    }


def _source_counts(store: CheckpointStore) -> dict[str, int | None]:
    return {
        code: progress.get("result_count")
        for code, progress in sorted(store.data["discovery"]["scope_progress"].items())
    }


def run_checkpointed_mode(args: argparse.Namespace, client: TunisieIndustrieClient) -> int:
    started = time.perf_counter()
    try:
        store = CheckpointStore(args.state_dir, resume=args.resume, reset=args.reset_state)
        discovery = discover_to_checkpoint(
            client,
            store,
            scope_codes=args.scope_codes,
            max_pages=args.max_pages,
        )
    except (StateError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 2

    if args.discover_only:
        elapsed = time.perf_counter() - started
        print("mode=discover-only")
        print(f"search_scopes_discovered={len(discovery.form.scopes)}")
        print(f"selected_scopes={','.join(scope.code for scope in discovery.scopes)}")
        print(f"all_scope_option={discovery.form.has_all_scope}")
        print(f"pages_processed_this_run={discovery.pages_processed_this_run}")
        print(f"pages_processed_total={store.pages_processed}")
        print(f"source_reported_counts={json.dumps(_source_counts(store), sort_keys=True)}")
        print(f"unique_companies_discovered={len(store.data['candidates'])}")
        print(f"duplicate_discoveries_removed={store.duplicate_discoveries_removed}")
        print(f"elapsed_seconds={elapsed:.2f}")
        print(f"state_dir={args.state_dir}")
        return 0

    if args.full_crawl:
        candidate_keys = store.candidate_keys()
        mode = "full-crawl"
    else:
        candidate_keys = _phase2_candidate_keys(store, discovery, args.limit)
        mode = "cross-scope-sample"

    scrape_summary = scrape_checkpointed(client, store, candidate_keys)
    records = store.records_sorted()
    if not records:
        LOGGER.error("No successfully scraped companies are available for export")
        return 1
    export_records(records, args.output)
    workbook_summary = verify_workbook(args.output, len(records))
    try:
        simple_result = export_simple_if_requested(records, args)
    except (SimpleExportError, AssertionError):
        return 2
    elapsed = time.perf_counter() - started
    print(f"mode={mode}")
    print(f"search_scopes_discovered={len(discovery.form.scopes)}")
    print(f"selected_scopes={','.join(scope.code for scope in discovery.scopes)}")
    print(f"pages_processed_this_run={discovery.pages_processed_this_run}")
    print(f"pages_processed_total={store.pages_processed}")
    print(f"source_reported_counts={json.dumps(_source_counts(store), sort_keys=True)}")
    print(f"unique_companies_discovered={len(store.data['candidates'])}")
    print(f"companies_fetched_this_run={scrape_summary['fetched']}")
    print(f"companies_successfully_scraped_this_run={scrape_summary['scraped']}")
    print(f"companies_skipped_already_completed={scrape_summary['skipped_completed']}")
    print(f"companies_failed={len(store.failures)}")
    print(f"duplicate_discoveries_removed={store.duplicate_discoveries_removed}")
    print(f"rows_exported={len(records)}")
    print(f"elapsed_seconds={elapsed:.2f}")
    print(f"output={args.output}")
    print(f"workbook_verification={workbook_summary}")
    if simple_result is not None:
        simple_report, simple_summary = simple_result
        print(f"simple_output={args.simple_output}")
        print(f"simple_validation={simple_report}")
        print(f"simple_workbook_verification={simple_summary}")
    print(f"state_dir={args.state_dir}")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe Phase 1/2 Tunisie Industrie scraper")
    parser.add_argument("--limit", type=int, default=10, help="Bounded sample size; for --sample-scopes, size per scope")
    parser.add_argument("--sector", default=DEFAULT_SEARCH_SECTOR, help="Phase 1 sector code; default remains 05")
    parser.add_argument("--scope", dest="scope_codes", action="append", help="Phase 2 scope code; repeatable")
    parser.add_argument("--discover-only", action="store_true", help="Discover scopes/results into checkpoint state only")
    parser.add_argument("--sample-scopes", action="store_true", help="Scrape --limit companies per selected scope")
    parser.add_argument("--full-crawl", action="store_true", help="Explicitly enable the long-running complete crawl")
    parser.add_argument("--max-pages", type=int, help="Maximum result pages per scope for controlled validation")
    parser.add_argument("--resume", action="store_true", help="Resume an existing checkpoint and retry pending failures")
    parser.add_argument("--reset-state", action="store_true", help="Explicitly reset the selected local state files")
    parser.add_argument("--state-dir", type=Path, default=Path("state") / "full", help="Checkpoint directory")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="XLSX output path")
    parser.add_argument(
        "--simple-template",
        type=Path,
        help="Canonical SIMPLE Companies XLSX export used as the CRM schema contract",
    )
    parser.add_argument(
        "--simple-output",
        type=Path,
        default=Path("output") / "simple_companies.xlsx",
        help="SIMPLE-compatible XLSX output path",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests in seconds")
    parser.add_argument("--retries", type=int, default=2, help="Bounded retries for transient failures")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds")
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="Disable TLS certificate verification only for the current server's expired certificate",
    )
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_PHASE1_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_PHASE1_LIMIT} for bounded modes")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be positive")
    if args.delay < 0 or args.retries < 0 or args.timeout <= 0:
        parser.error("--delay must be non-negative, --retries non-negative, and --timeout positive")
    if args.full_crawl and (args.discover_only or args.sample_scopes):
        parser.error("--full-crawl cannot be combined with --discover-only or --sample-scopes")
    if args.reset_state and args.resume:
        parser.error("--reset-state cannot be combined with --resume")
    if args.simple_output != Path("output") / "simple_companies.xlsx" and args.simple_template is None:
        parser.error("--simple-output requires --simple-template")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = ScraperConfig(
        timeout_seconds=args.timeout,
        request_delay_seconds=args.delay,
        max_retries=args.retries,
        verify_tls=not args.insecure_tls,
    )
    client = TunisieIndustrieClient(config)

    phase2_mode = bool(
        args.full_crawl
        or args.discover_only
        or args.sample_scopes
        or args.resume
        or args.reset_state
        or args.max_pages is not None
        or args.scope_codes
    )
    if phase2_mode:
        if args.resume and not (args.full_crawl or args.discover_only or args.sample_scopes):
            parser.error("--resume requires --full-crawl, --discover-only, or --sample-scopes")
        if args.reset_state and not (args.full_crawl or args.discover_only or args.sample_scopes):
            parser.error("--reset-state requires --full-crawl, --discover-only, or --sample-scopes")
        return run_checkpointed_mode(args, client)

    # Default command remains the conservative Phase 1 workflow.
    started = time.perf_counter()
    records, stats = scrape_companies(client, args.limit, args.sector)
    if not records:
        LOGGER.error("No companies were successfully parsed; no workbook was written")
        return 1
    export_records(records, args.output)
    workbook_summary = verify_workbook(args.output, len(records))
    try:
        simple_result = export_simple_if_requested(records, args)
    except (SimpleExportError, AssertionError):
        return 2
    elapsed = time.perf_counter() - started
    stats.exported = len(records)
    print(f"requested_limit={stats.requested_limit}")
    print(f"discovered={stats.discovered}")
    print(f"fetched={stats.fetched}")
    print(f"successfully_parsed={stats.successfully_parsed}")
    print(f"exported={stats.exported}")
    print(f"failed={stats.failed}")
    print(f"elapsed_seconds={elapsed:.2f}")
    print(f"output={args.output}")
    print(f"workbook_verification={workbook_summary}")
    if simple_result is not None:
        simple_report, simple_summary = simple_result
        print(f"simple_output={args.simple_output}")
        print(f"simple_validation={simple_report}")
        print(f"simple_workbook_verification={simple_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
