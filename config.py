from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_BASE_URL = "https://www.tunisieindustrie.nat.tn"
DEFAULT_DIRECTORY_PATH = "/en/dbi.asp"
DEFAULT_USER_AGENT = "TunisieIndustrieResearchScraper/0.1 (+public research client)"


@dataclass(frozen=True)
class ScraperConfig:
    base_url: str = DEFAULT_BASE_URL
    directory_path: str = DEFAULT_DIRECTORY_PATH
    timeout_seconds: float = 30.0
    request_delay_seconds: float = 1.0
    max_retries: int = 2
    backoff_factor: float = 1.5
    verify_tls: bool = True
    user_agent: str = DEFAULT_USER_AGENT

    @property
    def directory_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.directory_path}"


DEFAULT_OUTPUT_PATH = Path("output") / "tunisie_industrie_raw.xlsx"
DEFAULT_SEARCH_SECTOR = "05"
MAX_PHASE1_LIMIT = 100

