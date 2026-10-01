from __future__ import annotations

import re
from html import unescape
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from models import CompanyCandidate, CompanyRecord


class ParseError(ValueError):
    """Raised when a page does not contain the expected source structure."""


SOURCE_FIELD_ORDER = (
    "Short Name",
    "Company Name",
    "Manager",
    "Activities",
    "Products",
    "Factory's Address",
    "Gouvernorate",
    "Delegation",
    "Phone Number Head Office/Factory",
    "Fax Number Head Office/Factory",
    "E-mail",
    "URL",
    "Market",
    "Foreign Participant Country",
    "Created",
    "Share Capital DT",
    "Employees",
)

LABEL_TO_FIELD = {
    "short name": "short_name",
    "company name": "company_name",
    "manager": "manager",
    "activities": "activities",
    "products": "products",
    "factory's address": "factory_address",
    "factory address": "factory_address",
    "gouvernorate": "governorate",
    "delegation": "delegation",
    "phone number head office/factory": "phone",
    "telephone number head office/factory": "phone",
    "fax number head office/factory": "fax",
    "e-mail": "email",
    "email": "email",
    "url": "url",
    "website": "url",
    "market": "market",
    "foreign participant country": "foreign_participant_country",
    "created": "created",
    "share capital dt": "share_capital_dt",
    "employees": "employees",
}


def detect_encoding(content: bytes, content_type: str | None = None) -> str:
    """Detect the site's legacy encoding from headers/meta, then use Latin-1.

    The target server currently omits a charset in its HTTP header and its
    HTML declares/uses the older Latin-1 family. Latin-1 is intentionally the
    final fallback rather than UTF-8, so bytes such as French accents survive.
    """

    if content.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    header_match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type or "", re.I)
    if header_match:
        return header_match.group(1)
    head = content[:8192].decode("ascii", errors="ignore")
    meta_match = re.search(r"<meta[^>]+charset\s*=\s*['\"]?([\w.-]+)", head, re.I)
    if meta_match:
        return meta_match.group(1)
    http_equiv_match = re.search(
        r"<meta[^>]+content\s*=\s*['\"][^'\"]*charset\s*=\s*([\w.-]+)",
        head,
        re.I,
    )
    if http_equiv_match:
        return http_equiv_match.group(1)
    return "iso-8859-1"


def decode_html(content: bytes, content_type: str | None = None) -> str:
    encoding = detect_encoding(content, content_type)
    try:
        return content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")


def _clean_text(value: str) -> str:
    # Collapse ordinary HTML whitespace while preserving internal NBSPs.
    # The French representation of Share Capital uses U+00A0 between digit
    # groups, and that character is meaningful source data.
    text = unescape(value)
    text = re.sub(r"[ \t\r\n\f\v]+", " ", text)
    return text.strip()


def _label_key(value: str) -> str:
    return _clean_text(value).lower()


def parse_discovery_page(html: str, page_url: str) -> list[CompanyCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[CompanyCandidate] = []
    for row in soup.find_all("tr", onclick=True):
        onclick = row.get("onclick", "")
        match = re.search(r"dbi\.asp\?action=result&ident=([^'\"&\s)]+)", onclick, re.I)
        if not match:
            continue
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        values = [_clean_text(cell.get_text(" ", strip=True)) or None for cell in cells]
        href = f"dbi.asp?action=result&ident={match.group(1)}"
        candidates.append(
            CompanyCandidate(
                source_id=match.group(1),
                detail_url=urljoin(page_url, href),
                short_name=values[0] if len(values) > 0 else None,
                activities=values[1] if len(values) > 1 else None,
                phone=values[2] if len(values) > 2 else None,
                governorate=values[3] if len(values) > 3 else None,
            )
        )
    return candidates


def deduplicate_candidates(candidates: Iterable[CompanyCandidate]) -> list[CompanyCandidate]:
    result: list[CompanyCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.identity in seen:
            continue
        seen.add(candidate.identity)
        result.append(candidate)
    return result


def next_page_url(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        href = link["href"]
        parsed = urlsplit(href)
        query = parse_qs(parsed.query)
        if query.get("action") == ["search"] and "pagenum" in query:
            return urljoin(page_url, href)
    return None


def _value_from_cell(cell, field: str, page_url: str) -> str | None:
    anchor = cell.find("a", href=True)
    visible = _clean_text(cell.get_text(" ", strip=True))
    if field == "email" and anchor:
        href = anchor.get("href", "")
        if href.lower().startswith("mailto:"):
            return _clean_text(href[7:]) or visible or None
    if field == "url" and anchor:
        href = anchor.get("href", "").strip()
        if href and not href.lower().startswith(("mailto:", "javascript:")) and href != "#":
            return urljoin(page_url, href)
    return visible or None


def parse_company_detail(html: str, source_url: str, source_id: str | None = None) -> CompanyRecord:
    soup = BeautifulSoup(html, "html.parser")
    detail_table = None
    for table in soup.find_all("table"):
        labels = {_label_key(cell.get_text(" ", strip=True)) for cell in table.find_all("td")}
        if "short name" in labels and "company name" in labels:
            detail_table = table
            break
    if detail_table is None:
        raise ParseError(f"No company detail table found at {source_url}")

    values: dict[str, str | None] = {}
    for row in detail_table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        field = LABEL_TO_FIELD.get(_label_key(cells[0].get_text(" ", strip=True)))
        if field:
            values[field] = _value_from_cell(cells[1], field, source_url)

    if not values.get("short_name") and not values.get("company_name"):
        raise ParseError(f"Company detail table has no identity at {source_url}")
    if source_id is None:
        source_id = parse_qs(urlsplit(source_url).query).get("ident", [None])[0]
    return CompanyRecord(**values, source_id=source_id, source_url=source_url)


def parse_share_capital(html: str, page_url: str) -> str | None:
    """Extract the source site's share-capital value from English or French HTML."""

    soup = BeautifulSoup(html, "html.parser")
    labels = {"share capital dt", "capital en dt"}
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue
        if _label_key(cells[0].get_text(" ", strip=True)) in labels:
            return _value_from_cell(cells[1], "share_capital_dt", page_url)
    return None
