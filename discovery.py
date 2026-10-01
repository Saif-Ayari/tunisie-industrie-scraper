from __future__ import annotations

import logging
from dataclasses import dataclass

from client import TunisieIndustrieClient
from models import SearchForm, SearchScope
from parser import decode_html, parse_result_page
from state import CheckpointStore


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryRun:
    form: SearchForm
    scopes: tuple[SearchScope, ...]
    pages_processed_this_run: int
    candidates_seen_this_run: int
    duplicates_removed_this_run: int


def select_scopes(form: SearchForm, scope_codes: list[str] | None = None) -> tuple[SearchScope, ...]:
    if not scope_codes:
        return form.scopes
    by_code = {scope.code: scope for scope in form.scopes}
    unknown = [code for code in scope_codes if code not in by_code]
    if unknown:
        raise ValueError(f"Unknown scope code(s): {', '.join(unknown)}")
    return tuple(by_code[code] for code in scope_codes)


def discover_to_checkpoint(
    client: TunisieIndustrieClient,
    store: CheckpointStore,
    scope_codes: list[str] | None = None,
    max_pages: int | None = None,
) -> DiscoveryRun:
    form = client.fetch_search_form()
    scopes = select_scopes(form, scope_codes)
    store.set_scopes(scopes)
    pages_processed = 0
    candidates_seen = 0
    duplicates_before = store.duplicate_discoveries_removed

    for scope in scopes:
        progress = store.scope_progress(scope.code)
        if progress.get("complete"):
            continue
        start_page = int(progress.get("last_page", 0)) + 1
        if max_pages is not None and start_page > max_pages:
            continue
        response = client.search_scope_page(scope, start_page)
        current_page = start_page
        while True:
            html = decode_html(response.content, response.headers.get("Content-Type"))
            parsed = parse_result_page(html, response.url)
            page_number = parsed.page_number or current_page
            candidates_seen += len(parsed.candidates)
            for candidate in parsed.candidates:
                store.add_candidate(candidate, scope.code)
            has_next = bool(parsed.next_url)
            reached_page_limit = max_pages is not None and page_number >= max_pages
            reached_source_end = parsed.total_pages is not None and page_number >= parsed.total_pages
            complete = not has_next or reached_source_end
            if reached_page_limit and has_next and not reached_source_end:
                complete = False
            store.note_page(
                scope,
                page_number,
                parsed.result_count,
                parsed.total_pages,
                parsed.page_size,
                complete,
            )
            pages_processed += 1
            LOGGER.info(
                "Discovery scope=%s page=%s rows=%s source_count=%s unique=%s",
                scope.code,
                page_number,
                parsed.page_size,
                parsed.result_count,
                len(store.data["candidates"]),
            )
            if complete or reached_page_limit or not parsed.next_url:
                break
            current_page = page_number + 1
            response = client.request("GET", parsed.next_url, headers={"Referer": response.url})

    return DiscoveryRun(
        form=form,
        scopes=scopes,
        pages_processed_this_run=pages_processed,
        candidates_seen_this_run=candidates_seen,
        duplicates_removed_this_run=store.duplicate_discoveries_removed - duplicates_before,
    )

