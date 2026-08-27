from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from paper_radar.models import Paper
from paper_radar.sources.base import FetchContext
from paper_radar.utils import clean_text, in_window, isoformat, parse_datetime


class HuggingFaceSource:
    id = "huggingface_papers"
    endpoint = "https://huggingface.co/api/daily_papers"

    def fetch(self, context: FetchContext) -> list[Paper]:
        token = os.environ.get("HF_TOKEN", "").strip()
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        papers: list[Paper] = []
        day = context.since.date()
        last_day = context.until.date()
        per_day = min(100, max(20, context.limit))
        while day <= last_day and len(papers) < context.limit:
            body = context.http.get(
                self.endpoint,
                params={
                    "p": 0,
                    "limit": per_day,
                    "date": day.isoformat(),
                    "sort": "publishedAt",
                },
                headers=headers,
                ttl_seconds=1800,
            )
            papers.extend(self.parse(context.http.json(body), context))
            day += timedelta(days=1)
        return papers[: context.limit]

    def parse(self, payload: object, context: FetchContext) -> list[Paper]:
        if isinstance(payload, dict):
            items = payload.get("results") or payload.get("papers") or payload.get("items") or []
        else:
            items = payload
        if not isinstance(items, list):
            return []

        papers: list[Paper] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            nested = item.get("paper")
            record: dict[str, Any] = nested if isinstance(nested, dict) else item
            arxiv_id = clean_text(str(record.get("id") or record.get("paperId") or item.get("id") or ""))
            if not arxiv_id:
                continue
            title = clean_text(str(record.get("title") or item.get("title") or ""))
            if not title:
                continue
            published = parse_datetime(
                item.get("publishedAt") or record.get("publishedAt") or record.get("published_at")
            )
            if not in_window(published, context.since, context.until):
                continue
            authors = self._authors(record.get("authors"))
            summary = clean_text(
                str(
                    record.get("summary")
                    or record.get("ai_summary")
                    or record.get("aiSummary")
                    or ""
                )
            )
            github_url = clean_text(str(record.get("githubRepo") or item.get("githubRepo") or ""))
            project_url = clean_text(str(record.get("projectPage") or item.get("projectPage") or ""))
            upvotes = item.get("upvotes") or record.get("upvotes") or record.get("numUpvotes") or 0
            papers.append(
                Paper(
                    canonical_id=f"arxiv:{arxiv_id}",
                    title=title,
                    abstract=summary,
                    authors=authors,
                    published_at=isoformat(published),
                    url=f"https://huggingface.co/papers/{arxiv_id}",
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                    source=self.id,
                    source_ids={"arxiv": arxiv_id, "huggingface": arxiv_id},
                    metadata={
                        "hf_upvotes": int(upvotes) if str(upvotes).isdigit() else 0,
                        "github_url": github_url,
                        "project_url": project_url,
                    },
                )
            )
        return papers

    @staticmethod
    def _authors(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        authors: list[str] = []
        for author in value:
            if isinstance(author, dict):
                name = author.get("name") or author.get("fullname") or author.get("user")
            else:
                name = author
            if name:
                authors.append(clean_text(str(name)))
        return authors
