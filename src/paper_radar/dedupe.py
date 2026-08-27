from __future__ import annotations

import re
from difflib import SequenceMatcher

from paper_radar.models import Paper


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return _NON_ALNUM.sub(" ", title.lower()).strip()


def deduplicate(papers: list[Paper], *, title_threshold: float = 0.94) -> list[Paper]:
    unique: list[Paper] = []
    identity_index: dict[str, int] = {}
    normalized_titles: list[str] = []

    for paper in papers:
        keys = _identity_keys(paper)
        match_index = next((identity_index[key] for key in keys if key in identity_index), None)
        normalized = normalize_title(paper.title)
        if match_index is None and normalized:
            match_index = _fuzzy_title_match(normalized, normalized_titles, title_threshold)

        if match_index is None:
            match_index = len(unique)
            unique.append(paper)
            normalized_titles.append(normalized)
        else:
            unique[match_index].merge(paper)

        for key in keys | _identity_keys(unique[match_index]):
            identity_index[key] = match_index
    return unique


def _identity_keys(paper: Paper) -> set[str]:
    keys = {paper.canonical_id.lower()}
    for kind in ("doi", "arxiv", "openreview", "pubmed"):
        value = paper.source_ids.get(kind)
        if value:
            keys.add(f"{kind}:{value.lower()}")
    normalized = normalize_title(paper.title)
    if normalized:
        keys.add(f"title:{normalized}")
    return keys


def _fuzzy_title_match(title: str, existing: list[str], threshold: float) -> int | None:
    if len(title) < 24:
        return None
    prefix = title[:12]
    for index, candidate in enumerate(existing):
        if candidate[:12] != prefix:
            continue
        if SequenceMatcher(None, title, candidate).ratio() >= threshold:
            return index
    return None

