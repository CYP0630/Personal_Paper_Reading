from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    raw: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, path: str | Path) -> "ResearchConfig":
        resolved = Path(path).expanduser().resolve()
        data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError(f"Unsupported or missing schema_version in {resolved}")
        return cls(raw=data, path=resolved)

    @property
    def topics(self) -> list[dict[str, Any]]:
        groups = self.raw.get("topics", {})
        topics = [*groups.get("methods", []), *groups.get("domains", [])]
        return [topic for topic in topics if topic.get("enabled", True)]

    @property
    def topic_by_id(self) -> dict[str, dict[str, Any]]:
        return {topic["id"]: topic for topic in self.topics}

    @property
    def journal_watch(self) -> list[dict[str, Any]]:
        journals = self.raw.get("source_groups", {}).get("journal_watch", [])
        return [journal for journal in journals if journal.get("enabled", True)]

    @property
    def enabled_sources(self) -> set[str]:
        groups = self.raw.get("source_groups", {})
        sources: set[str] = set()
        for group in ("scholarly_search", "trend_detection", "domain_sources"):
            for source in groups.get(group, []):
                if source.get("enabled", True):
                    sources.add(source["id"])
        if self.journal_watch:
            sources.add("nature")
        return sources

    def daily_settings(self) -> dict[str, Any]:
        return self.raw["retrieval_policy"]["daily"]

    def scoring_settings(self) -> dict[str, Any]:
        return self.raw["retrieval_policy"]["scoring"]

    def selection_settings(self) -> dict[str, Any]:
        return self.raw["retrieval_policy"]["selection"]

