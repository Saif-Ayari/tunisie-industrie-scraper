from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from client import TunisieIndustrieClient
from config import DEFAULT_OUTPUT_PATH, DEFAULT_SEARCH_SECTOR, MAX_PHASE1_LIMIT, ScraperConfig
from exporter import export_records, verify_workbook
from models import CompanyRecord, ScrapeStats
from normalizer import apply_verified_share_capital, normalize_record
from parser import decode_html, parse_company_detail


LOGGER = logging.getLogger("tunisie_industrie_scraper")


def scrape_companies(client: TunisieIndustrieClient, limit: int, sector: str) -> tuple[list[CompanyRecord], ScrapeStats]:
    stats = ScrapeStats(requested_limit=limit)
    criteria = {"secteur": sector, "action": "search"}
    candidates = client.discover(limit, criteria)
    stats.discovered = len(candidates)
    records: list[CompanyRecord] = []
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    for candidate in candidates:
        stats.fetched += 1
        try:
            response = client.request("GET", candidate.detail_url)
            html = decode_html(response.content, response.headers.get("Content-Type"))
            record = parse_company_detail(html, response.url, candidate.source_id)
            if record.share_capital_dt and "?" in record.share_capital_dt and candidate.source_id:
                try:
                    verified_capital = client.fetch_same_record_french_share_capital(candidate.source_id)
                    corrected = apply_verified_share_capital(record, verified_capital)
                    if corrected.share_capital_dt != record.share_capital_dt:
                        LOGGER.info(
                            "Corrected Share Capital DT for Source ID %s from the same record's French representation",
                            candidate.source_id,
                        )
                    record = corrected
                except Exception:  # noqa: BLE001 - preserve the original source value if corroboration fails.
                    LOGGER.exception("Could not corroborate Share Capital DT for Source ID %s", candidate.source_id)
            record.scraped_at = scraped_at
            records.append(normalize_record(record))
        except Exception as exc:  # noqa: BLE001 - a malformed public record should not abort the batch.
            stats.failed += 1
            stats.failures.append(candidate.detail_url)
            LOGGER.exception("Failed to parse %s: %s", candidate.detail_url, exc)
    stats.successfully_parsed = len(records)
    return records, stats


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Phase 1 Tunisie Industrie scraper")
    parser.add_argument("--limit", type=int, default=10, help="Maximum companies to export (Phase 1 max: 100)")
    parser.add_argument("--sector", default=DEFAULT_SEARCH_SECTOR, help="Directory sector code used for discovery")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="XLSX output path")
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
        parser.error(f"--limit must be between 1 and {MAX_PHASE1_LIMIT} for Phase 1")
    if args.delay < 0 or args.retries < 0 or args.timeout <= 0:
        parser.error("--delay must be non-negative, --retries non-negative, and --timeout positive")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = ScraperConfig(
        timeout_seconds=args.timeout,
        request_delay_seconds=args.delay,
        max_retries=args.retries,
        verify_tls=not args.insecure_tls,
    )
    started = time.perf_counter()
    client = TunisieIndustrieClient(config)
    records, stats = scrape_companies(client, args.limit, args.sector)
    if not records:
        LOGGER.error("No companies were successfully parsed; no workbook was written")
        return 1
    export_records(records, args.output)
    workbook_summary = verify_workbook(args.output, len(records))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
