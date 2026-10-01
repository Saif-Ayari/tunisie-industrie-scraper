from __future__ import annotations

from dataclasses import replace
import re
from urllib.parse import urldefrag, urlsplit, urlunsplit

from models import CompanyRecord


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[ \t\r\n\f\v]+", " ", value).strip()
    return cleaned or None


def canonical_source_url(url: str) -> str:
    without_fragment, _ = urldefrag(url)
    parts = urlsplit(without_fragment)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def normalize_record(record: CompanyRecord) -> CompanyRecord:
    fields = (
        "short_name",
        "company_name",
        "manager",
        "activities",
        "products",
        "factory_address",
        "governorate",
        "delegation",
        "phone",
        "fax",
        "email",
        "url",
        "market",
        "foreign_participant_country",
        "created",
        "share_capital_dt",
        "employees",
        "source_id",
    )
    values = {field: normalize_text(getattr(record, field)) for field in fields}
    values["source_url"] = canonical_source_url(record.source_url)
    values["scraped_at"] = record.scraped_at.strip()
    return replace(record, **values)


def apply_verified_share_capital(record: CompanyRecord, verified_value: str | None) -> CompanyRecord:
    """Use a same-record source-language value to repair a known bad variant.

    The English endpoint emits literal question marks for grouped capital
    values. The French endpoint for the same source ID emits the actual NBSP
    separator. This function only accepts a corroborating value containing
    digits and no question mark; it never guesses a replacement character.
    """

    verified = normalize_text(verified_value)
    current = record.share_capital_dt
    if (
        current
        and "?" in current
        and verified
        and "?" not in verified
        and any(character.isdigit() for character in verified)
    ):
        return replace(record, share_capital_dt=verified)
    return record
