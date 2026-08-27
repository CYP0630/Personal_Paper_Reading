from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from paper_radar.config import ResearchConfig
from paper_radar.models import Paper


_TOKEN = re.compile(r"[a-z0-9][a-z0-9+._-]*")


def score_papers(papers: list[Paper], config: ResearchConfig, *, now: datetime) -> list[Paper]:
    global_excludes = [str(term).lower() for term in config.raw["query_defaults"].get("global_exclude_terms", [])]
    ai_terms = [str(term).lower() for term in config.raw["query_defaults"].get("ai_context_terms", [])]
    fit_weights = config.scoring_settings()["fit"]
    heat_weights = config.scoring_settings()["heat"]
    confidence_weights = config.scoring_settings()["confidence"]

    for paper in papers:
        text = f"{paper.title} {paper.abstract} {' '.join(paper.categories)}".lower()
        if any(term in text for term in global_excludes):
            paper.scores = {"fit": 0.0, "heat": 0.0, "confidence": 0.0, "rank": 0.0}
            continue

        topic_details: list[tuple[str, float, list[str], str | None]] = []
        for topic in config.topics:
            detail = _topic_score(paper, topic, text, ai_terms, fit_weights)
            if detail[0] > 0:
                topic_details.append((topic["id"], *detail))
        topic_details.sort(key=lambda item: item[1], reverse=True)
        paper.topics = [topic_id for topic_id, fit, _, _ in topic_details if fit >= 0.42]
        paper.matched_terms = {
            topic_id: terms for topic_id, fit, terms, _ in topic_details if fit >= 0.42 and terms
        }
        fit = topic_details[0][1] if topic_details else 0.0

        heat = _heat_score(paper, now, heat_weights)
        confidence = _confidence_score(paper, confidence_weights)
        journal_boost = float(paper.metadata.get("journal_score_boost", 0.0))
        rank = min(1.0, 0.70 * fit + 0.20 * heat + 0.10 * confidence + journal_boost)
        paper.scores = {
            "fit": round(fit, 4),
            "heat": round(heat, 4),
            "confidence": round(confidence, 4),
            "rank": round(rank, 4),
        }
        paper.reasons = _reasons(paper, topic_details)
    return papers


def select_digest(papers: list[Paper], config: ResearchConfig, *, size: int) -> list[Paper]:
    minimum_fit = float(config.selection_settings().get("minimum_fit", 0.55))
    max_per_topic = int(config.selection_settings().get("maximum_per_primary_topic", 3))
    eligible = sorted(
        (paper for paper in papers if paper.topics and paper.scores.get("fit", 0.0) >= minimum_fit),
        key=lambda paper: paper.scores.get("rank", 0.0),
        reverse=True,
    )
    selected: list[Paper] = []
    selected_ids: set[str] = set()
    topic_counts: dict[str, int] = {}

    trend_target = min(2, size)
    cross_target = min(1, max(0, size - trend_target))
    trend = [paper for paper in eligible if _is_trending(paper)]
    cross = [paper for paper in eligible if len(paper.topics) > 1]
    _take(trend, trend_target, "trend", selected, selected_ids, topic_counts, max_per_topic)
    _take(
        cross,
        len(selected) + cross_target,
        "cross_topic",
        selected,
        selected_ids,
        topic_counts,
        max_per_topic,
    )
    _take(eligible, size, "core_fit", selected, selected_ids, topic_counts, max_per_topic)

    if len(selected) < size:
        _take(eligible, size, "core_fit", selected, selected_ids, topic_counts, 10_000)
    selected.sort(key=lambda paper: paper.scores.get("rank", 0.0), reverse=True)
    return selected[:size]


def _topic_score(
    paper: Paper,
    topic: dict[str, Any],
    text: str,
    ai_terms: list[str],
    weights: dict[str, float],
) -> tuple[float, list[str], str | None]:
    excludes = [str(term).lower() for term in topic.get("exclude_terms", [])]
    if any(term in text for term in excludes):
        return 0.0, [], None
    if topic.get("require_ai_context") and not any(term.lower() in text for term in ai_terms):
        return 0.0, [], None

    terms = [str(term) for term in topic.get("include_terms", [])]
    matched = [term for term in terms if term.lower() in text]
    # Exact phrase coverage is the precision gate. Token overlap and seed
    # similarity refine an already plausible topic; they do not create one.
    if not matched:
        return 0.0, [], None
    text_tokens = set(_TOKEN.findall(text))
    term_token_scores = []
    for term in terms:
        tokens = set(_TOKEN.findall(term.lower()))
        if tokens:
            term_token_scores.append(len(tokens & text_tokens) / len(tokens))
    semantic = max(term_token_scores, default=0.0)
    keyword = min(1.0, 0.65 + 0.18 * (len(matched) - 1)) if matched else 0.0

    seed_score = 0.0
    best_seed: str | None = None
    for seed in topic.get("seeds", {}).get("positive", []):
        seed_text = f"{seed.get('title', '')} {' '.join(seed.get('tags', []))}".lower()
        seed_tokens = set(_TOKEN.findall(seed_text))
        if not seed_tokens:
            continue
        overlap = len(seed_tokens & text_tokens) / len(seed_tokens | text_tokens)
        scaled = min(1.0, overlap * 5.0)
        if scaled > seed_score:
            seed_score = scaled
            best_seed = str(seed.get("title", ""))

    category_match = bool(set(paper.categories) & set(topic.get("arxiv_categories", [])))
    venue_match = paper.venue and any(
        venue.lower() in paper.venue.lower() for venue in topic.get("preferred_venues", [])
    )
    project = 1.0 if venue_match else 0.7 if category_match else 0.25 if matched else 0.0
    score = (
        float(weights["semantic_similarity"]) * semantic
        + float(weights["keyword_and_topic_match"]) * keyword
        + float(weights["positive_seed_similarity"]) * seed_score
        + float(weights["project_relevance"]) * project
    )
    return min(1.0, score), matched, best_seed


def _heat_score(paper: Paper, now: datetime, weights: dict[str, float]) -> float:
    consensus = min(1.0, max(0, len(paper.discovered_by) - 1) / 2)
    published = paper.published_datetime
    if published:
        age_days = max(0.0, (now.astimezone(timezone.utc) - published).total_seconds() / 86400)
        age = math.exp(-age_days / 5.0)
    else:
        age = 0.25
    upvotes = max(0, int(paper.metadata.get("hf_upvotes", 0) or 0))
    attention = min(1.0, math.log1p(upvotes) / math.log(51))
    code = 0.35 if paper.metadata.get("github_url") else 0.0
    return min(
        1.0,
        float(weights["source_consensus"]) * consensus
        + float(weights["age_normalized_attention"]) * max(age, attention)
        + float(weights["citation_velocity"]) * attention
        + float(weights["code_or_model_velocity"]) * code,
    )


def _confidence_score(paper: Paper, weights: dict[str, float]) -> float:
    metadata = sum(bool(value) for value in (paper.title, paper.authors, paper.published_at, paper.url)) / 4
    full_text = 1.0 if paper.pdf_url else 0.4
    evidence = 1.0 if paper.source_ids.get("doi") else 0.85 if paper.abstract else 0.5
    reproducibility = 0.7 if paper.metadata.get("github_url") else 0.35
    return min(
        1.0,
        float(weights["metadata_completeness"]) * metadata
        + float(weights["full_text_availability"]) * full_text
        + float(weights["evidence_quality"]) * evidence
        + float(weights["reproducibility_signals"]) * reproducibility,
    )


def _reasons(
    paper: Paper,
    topic_details: list[tuple[str, float, list[str], str | None]],
) -> list[str]:
    reasons: list[str] = []
    if topic_details:
        topic_id, _, terms, seed = topic_details[0]
        if terms:
            reasons.append(f"匹配 {topic_id}: {', '.join(terms[:3])}")
        if seed:
            reasons.append(f"接近种子论文：{seed}")
    if len(paper.discovered_by) > 1:
        reasons.append(f"被 {len(paper.discovered_by)} 个来源同时发现")
    if "huggingface_papers" in paper.discovered_by:
        reasons.append("入选 Hugging Face Daily Papers")
    if paper.metadata.get("journal_score_boost"):
        reasons.append(f"来自重点期刊 {paper.venue}")
    return reasons[:4]


def _is_trending(paper: Paper) -> bool:
    return (
        "huggingface_papers" in paper.discovered_by
        or len(paper.discovered_by) > 1
        or int(paper.metadata.get("hf_upvotes", 0) or 0) > 0
    )


def _take(
    candidates: list[Paper],
    target_size: int,
    lane: str,
    selected: list[Paper],
    selected_ids: set[str],
    topic_counts: dict[str, int],
    max_per_topic: int,
) -> None:
    for paper in candidates:
        if len(selected) >= target_size:
            return
        if paper.canonical_id in selected_ids:
            continue
        primary = paper.topics[0]
        if topic_counts.get(primary, 0) >= max_per_topic:
            continue
        paper.metadata["selection_lane"] = lane
        selected.append(paper)
        selected_ids.add(paper.canonical_id)
        topic_counts[primary] = topic_counts.get(primary, 0) + 1
