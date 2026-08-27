from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from paper_radar.config import ResearchConfig
from paper_radar.dedupe import deduplicate
from paper_radar.http import HttpClient
from paper_radar.models import Paper
from paper_radar.scoring import score_papers, select_digest
from paper_radar.sources import ArxivSource, HuggingFaceSource, NatureSource, OpenReviewSource, PubMedSource
from paper_radar.sources.base import FetchContext, PaperSource


SOURCE_FACTORIES: dict[str, type[PaperSource]] = {
    "arxiv": ArxivSource,
    "openreview": OpenReviewSource,
    "pubmed": PubMedSource,
    "nature": NatureSource,
    "huggingface_papers": HuggingFaceSource,
}

SOURCE_ALIASES = {
    "huggingface": "huggingface_papers",
    "hf": "huggingface_papers",
}


@dataclass(slots=True)
class DiscoveryResult:
    generated_at: str
    window_start: str
    window_end: str
    source_counts: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)
    fetched_count: int = 0
    unique_count: int = 0
    eligible_count: int = 0
    selected: list[Paper] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "window": {"start": self.window_start, "end": self.window_end},
            "stats": {
                "source_counts": self.source_counts,
                "source_errors": self.source_errors,
                "fetched": self.fetched_count,
                "unique": self.unique_count,
                "eligible": self.eligible_count,
                "selected": len(self.selected),
            },
            "papers": [paper.to_dict() for paper in self.selected],
        }


def discover(
    config: ResearchConfig,
    *,
    since: datetime,
    until: datetime,
    cache_dir: Path,
    source_ids: set[str] | None = None,
    candidate_pool_size: int | None = None,
    digest_size: int | None = None,
    offline: bool = False,
) -> DiscoveryResult:
    requested = {_canonical_source(source_id) for source_id in source_ids} if source_ids else config.enabled_sources
    active = [source_id for source_id in SOURCE_FACTORIES if source_id in requested]
    if not active:
        raise ValueError(f"No implemented sources selected. Requested: {sorted(requested)}")

    pool_size = candidate_pool_size or int(config.daily_settings()["candidate_pool_size"])
    per_source_limit = max(30, pool_size // len(active))
    http = HttpClient(cache_dir=cache_dir, offline=offline)
    source_counts: dict[str, int] = {}
    source_errors: dict[str, str] = {}
    fetched: list[Paper] = []

    with ThreadPoolExecutor(max_workers=min(5, len(active))) as executor:
        futures = {}
        for source_id in active:
            source = SOURCE_FACTORIES[source_id]()
            context = FetchContext(
                config=config,
                http=http,
                since=since,
                until=until,
                limit=per_source_limit,
            )
            futures[executor.submit(source.fetch, context)] = source_id
        for future in as_completed(futures):
            source_id = futures[future]
            try:
                papers = future.result()
            except Exception as exc:  # Sources fail independently by design.
                source_counts[source_id] = 0
                source_errors[source_id] = str(exc)
                continue
            source_counts[source_id] = len(papers)
            fetched.extend(papers)

    threshold = float(
        config.raw["retrieval_policy"]["deduplication"].get(
            "normalized_title_similarity_threshold", 0.94
        )
    )
    unique = deduplicate(fetched, title_threshold=threshold)
    score_papers(unique, config, now=until)
    minimum_fit = float(config.selection_settings().get("minimum_fit", 0.55))
    eligible = [
        paper for paper in unique if paper.topics and paper.scores.get("fit", 0.0) >= minimum_fit
    ]
    selected = select_digest(
        eligible,
        config,
        size=digest_size or int(config.daily_settings()["digest_size"]),
    )
    return DiscoveryResult(
        generated_at=datetime.now().astimezone().isoformat(),
        window_start=since.isoformat(),
        window_end=until.isoformat(),
        source_counts=dict(sorted(source_counts.items())),
        source_errors=dict(sorted(source_errors.items())),
        fetched_count=len(fetched),
        unique_count=len(unique),
        eligible_count=len(eligible),
        selected=selected,
    )


def _canonical_source(source_id: str) -> str:
    value = source_id.strip().lower()
    return SOURCE_ALIASES.get(value, value)

