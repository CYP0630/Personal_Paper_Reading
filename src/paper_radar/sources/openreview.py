from __future__ import annotations

import re
from typing import Any

from paper_radar.models import Paper
from paper_radar.sources.base import FetchContext
from paper_radar.utils import clean_text, in_window, isoformat, openreview_value, parse_datetime


class OpenReviewSource:
    id = "openreview"
    endpoint = "https://api2.openreview.net/notes/search"
    arxiv_pattern = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5})", re.I)
    doi_pattern = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)

    def fetch(self, context: FetchContext) -> list[Paper]:
        query_terms = self._query_terms(context)
        per_query = max(10, min(50, context.limit // max(1, len(query_terms))))
        papers: list[Paper] = []
        seen: set[str] = set()
        for term in query_terms:
            body = context.http.get(
                self.endpoint,
                params={
                    "term": term,
                    "type": "terms",
                    "content": "all",
                    "source": "forum",
                    "sort": "tmdate:desc",
                    "limit": per_query,
                },
                headers={"Accept": "application/json"},
                ttl_seconds=1800,
            )
            for paper in self.parse(context.http.json(body), context):
                if paper.canonical_id not in seen:
                    seen.add(paper.canonical_id)
                    papers.append(paper)
        return papers[: context.limit]

    def parse(self, payload: object, context: FetchContext) -> list[Paper]:
        if not isinstance(payload, dict) or not isinstance(payload.get("notes"), list):
            return []
        papers: list[Paper] = []
        for note in payload["notes"]:
            if not isinstance(note, dict):
                continue
            content = note.get("content") if isinstance(note.get("content"), dict) else {}
            title = clean_text(str(openreview_value(content.get("title")) or ""))
            abstract = clean_text(str(openreview_value(content.get("abstract")) or ""))
            if not title:
                continue
            published = parse_datetime(note.get("pdate") or note.get("cdate"))
            if not in_window(published, context.since, context.until):
                continue
            note_id = clean_text(str(note.get("id") or note.get("forum") or ""))
            if not note_id:
                continue
            authors_value = openreview_value(content.get("authors"))
            authors = [clean_text(str(author)) for author in authors_value] if isinstance(authors_value, list) else []
            keywords_value = openreview_value(content.get("keywords"))
            keywords = [clean_text(str(keyword)) for keyword in keywords_value] if isinstance(keywords_value, list) else []
            venue = clean_text(
                str(openreview_value(content.get("venue")) or openreview_value(content.get("venueid")) or "")
            )
            source_ids = {"openreview": note_id}
            combined = " ".join(
                str(openreview_value(content.get(key)) or "")
                for key in ("arxiv_id", "arxiv", "paper_link", "doi", "venue")
            )
            arxiv_match = self.arxiv_pattern.search(combined)
            doi_match = self.doi_pattern.search(combined)
            if arxiv_match:
                source_ids["arxiv"] = arxiv_match.group(1)
            if doi_match:
                source_ids["doi"] = doi_match.group(0).rstrip(".,)").lower()
            papers.append(
                Paper(
                    canonical_id=f"openreview:{note_id}",
                    title=title,
                    abstract=abstract,
                    authors=authors,
                    published_at=isoformat(published),
                    updated_at=isoformat(parse_datetime(note.get("mdate"))),
                    url=f"https://openreview.net/forum?id={note_id}",
                    pdf_url=f"https://openreview.net/pdf?id={note_id}",
                    venue=venue or None,
                    source=self.id,
                    source_ids=source_ids,
                    categories=keywords,
                    metadata={"invitation": note.get("invitation", "")},
                )
            )
        return papers

    @staticmethod
    def _query_terms(context: FetchContext) -> list[str]:
        terms: list[str] = []
        for topic in context.config.topics:
            candidates = [str(term) for term in topic.get("include_terms", []) if len(str(term)) >= 5]
            terms.extend(candidates[:2])
        return list(dict.fromkeys(terms))

