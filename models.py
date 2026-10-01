from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompanyCandidate:
    source_id: str | None
    detail_url: str
    short_name: str | None = None
    activities: str | None = None
    phone: str | None = None
    governorate: str | None = None
    scopes: tuple[str, ...] = ()

    @property
    def identity(self) -> str:
        return self.source_id or self.detail_url


@dataclass(frozen=True)
class SearchScope:
    code: str
    label: str

    @property
    def criteria(self) -> dict[str, str]:
        return {"secteur": self.code, "action": "search"}


@dataclass(frozen=True)
class SearchForm:
    url: str
    method: str
    action: str
    hidden_fields: dict[str, str]
    controls: dict[str, tuple[tuple[str, str], ...]]
    scopes: tuple[SearchScope, ...]
    has_all_scope: bool


@dataclass(frozen=True)
class DiscoveryPage:
    candidates: tuple[CompanyCandidate, ...]
    page_number: int | None
    total_pages: int | None
    result_count: int | None
    next_url: str | None
    page_size: int


@dataclass
class CompanyRecord:
    short_name: str | None = None
    company_name: str | None = None
    manager: str | None = None
    activities: str | None = None
    products: str | None = None
    factory_address: str | None = None
    governorate: str | None = None
    delegation: str | None = None
    phone: str | None = None
    fax: str | None = None
    email: str | None = None
    url: str | None = None
    market: str | None = None
    foreign_participant_country: str | None = None
    created: str | None = None
    share_capital_dt: str | None = None
    employees: str | None = None
    source_id: str | None = None
    source_url: str = ""
    scraped_at: str = ""


@dataclass
class ScrapeStats:
    requested_limit: int
    discovered: int = 0
    fetched: int = 0
    successfully_parsed: int = 0
    exported: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)
