from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Paper:
    canonical_id: str
    title: str
    source: str
    url: str
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    published_at: str = ""
    updated_at: str = ""
    pdf_url: str | None = None
    venue: str | None = None
    source_ids: dict[str, str] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    discovered_by: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    matched_terms: dict[str, list[str]] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.discovered_by:
            self.discovered_by = [self.source]

    @property
    def published_datetime(self) -> datetime | None:
        if not self.published_at:
            return None
        value = self.published_at.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def merge(self, other: "Paper") -> None:
        """Merge another source's record into this paper without losing provenance."""
        self.discovered_by = sorted(set(self.discovered_by + other.discovered_by))
        self.authors = list(dict.fromkeys(self.authors + other.authors))
        self.categories = sorted(set(self.categories + other.categories))
        self.source_ids.update({k: v for k, v in other.source_ids.items() if v})
        self.metadata.update({k: v for k, v in other.metadata.items() if v not in (None, "", [], {})})

        if len(other.abstract) > len(self.abstract):
            self.abstract = other.abstract
        if not self.pdf_url and other.pdf_url:
            self.pdf_url = other.pdf_url
        if not self.venue and other.venue:
            self.venue = other.venue
        if not self.published_at and other.published_at:
            self.published_at = other.published_at
        if not self.updated_at and other.updated_at:
            self.updated_at = other.updated_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

