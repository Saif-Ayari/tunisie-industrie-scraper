from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests
from requests import Response
from urllib3.exceptions import InsecureRequestWarning

from config import ScraperConfig
from models import CompanyCandidate, SearchForm, SearchScope
from normalizer import canonical_source_url
from parser import decode_html, deduplicate_candidates, next_page_url, parse_discovery_page, parse_search_form, parse_share_capital


LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class TunisieIndustrieClient:
    def __init__(self, config: ScraperConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent, "Accept": "text/html"})
        self._last_request_at = 0.0
        if not config.verify_tls:
            requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
            LOGGER.warning(
                "TLS certificate verification is disabled because the target server certificate is expired; "
                "use --verify-tls after the site certificate is renewed."
            )

    def _wait_before_request(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.config.request_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self._wait_before_request()
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=self.config.timeout_seconds,
                    verify=self.config.verify_tls,
                    allow_redirects=True,
                    **kwargs,
                )
                self._last_request_at = time.monotonic()
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.config.max_retries:
                    delay = self.config.backoff_factor * (2**attempt)
                    LOGGER.warning("HTTP %s for %s; retrying in %.1fs", response.status_code, url, delay)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                delay = self.config.backoff_factor * (2**attempt)
                LOGGER.warning("Request failed for %s (%s); retrying in %.1fs", url, exc, delay)
                time.sleep(delay)
        assert last_error is not None
        raise last_error

    def get_directory(self) -> Response:
        return self.request("GET", self.config.directory_url)

    def fetch_search_form(self) -> SearchForm:
        response = self.get_directory()
        html = decode_html(response.content, response.headers.get("Content-Type"))
        return parse_search_form(html, response.url)

    def search(self, criteria: dict[str, str]) -> Response:
        return self.request(
            "POST",
            self.config.directory_url,
            data=criteria,
            headers={"Referer": self.config.directory_url},
        )

    def search_scope_page(self, scope: SearchScope, page_number: int = 1) -> Response:
        initial = self.search(scope.criteria)
        if page_number <= 1:
            return initial
        page_url = urljoin(self.config.directory_url, f"?action=search&pagenum={page_number}")
        return self.request("GET", page_url, headers={"Referer": initial.url})

    def fetch_same_record_french_share_capital(self, source_id: str) -> str | None:
        """Read the same public record's French representation for one field."""

        french_url = f"{self.config.base_url.rstrip('/')}/fr/dbi.asp?action=result&ident={source_id}"
        response = self.request("GET", french_url)
        html = decode_html(response.content, response.headers.get("Content-Type"))
        return parse_share_capital(html, response.url)

    def discover(self, limit: int, criteria: dict[str, str]) -> list[CompanyCandidate]:
        self.get_directory()
        response = self.search(criteria)
        discovered: list[CompanyCandidate] = []
        visited_pages: set[str] = set()
        while response is not None and len(discovered) < limit:
            page_url = canonical_source_url(response.url)
            if page_url in visited_pages:
                LOGGER.warning("Stopping pagination loop at %s", page_url)
                break
            visited_pages.add(page_url)
            html = decode_html(response.content, response.headers.get("Content-Type"))
            discovered.extend(parse_discovery_page(html, response.url))
            discovered = deduplicate_candidates(discovered)
            if len(discovered) >= limit:
                break
            next_url = next_page_url(html, response.url)
            if not next_url:
                break
            response = self.request("GET", urljoin(response.url, next_url))
        return discovered[:limit]
