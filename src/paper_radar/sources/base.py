from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from paper_radar.config import ResearchConfig
from paper_radar.http import HttpClient
from paper_radar.models import Paper


@dataclass(frozen=True, slots=True)
class FetchContext:
    config: ResearchConfig
    http: HttpClient
    since: datetime
    until: datetime
    limit: int


class PaperSource(Protocol):
    id: str

    def fetch(self, context: FetchContext) -> list[Paper]: ...
