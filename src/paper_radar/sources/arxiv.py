from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from paper_radar.models import Paper
from paper_radar.sources.base import FetchContext
from paper_radar.utils import clean_text, in_window, isoformat, parse_datetime


class ArxivSource:
    id = "arxiv"
    endpoint = "https://export.arxiv.org/api/query"
    _id_pattern = re.compile(r"/abs/([^v]+)(?:v\d+)?$")

    def fetch(self, context: FetchContext) -> list[Paper]:
        categories = sorted(
            {
                category
                for topic in context.config.topics
                for category in topic.get("arxiv_categories", [])
            }
        )
        category_query = " OR ".join(f"cat:{category}" for category in categories)
        start = context.since.strftime("%Y%m%d%H%M")
        end = context.until.strftime("%Y%m%d%H%M")
        search_query = f"({category_query}) AND submittedDate:[{start} TO {end}]"
        body = context.http.get(
            self.endpoint,
            params={
                "search_query": search_query,
                "start": 0,
                "max_results": min(context.limit, 500),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            ttl_seconds=1800,
        )
        return self.parse(body, context)

    def parse(self, body: bytes, context: FetchContext) -> list[Paper]:
        root = ET.fromstring(body)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        papers: list[Paper] = []
        for entry in root.findall("atom:entry", ns):
            published = parse_datetime(entry.findtext("atom:published", namespaces=ns))
            if not in_window(published, context.since, context.until):
                continue
            identifier_url = clean_text(entry.findtext("atom:id", namespaces=ns))
            match = self._id_pattern.search(identifier_url)
            arxiv_id = match.group(1) if match else identifier_url.rsplit("/", 1)[-1]
            links = {
                link.attrib.get("title") or link.attrib.get("rel", ""): link.attrib.get("href", "")
                for link in entry.findall("atom:link", ns)
            }
            doi = clean_text(entry.findtext("arxiv:doi", namespaces=ns))
            source_ids = {"arxiv": arxiv_id}
            if doi:
                source_ids["doi"] = doi.lower()
            papers.append(
                Paper(
                    canonical_id=f"arxiv:{arxiv_id}",
                    title=clean_text(entry.findtext("atom:title", namespaces=ns)),
                    abstract=clean_text(entry.findtext("atom:summary", namespaces=ns)),
                    authors=[
                        clean_text(author.findtext("atom:name", namespaces=ns))
                        for author in entry.findall("atom:author", ns)
                    ],
                    published_at=isoformat(published),
                    updated_at=isoformat(parse_datetime(entry.findtext("atom:updated", namespaces=ns))),
                    url=links.get("alternate") or f"https://arxiv.org/abs/{arxiv_id}",
                    pdf_url=links.get("pdf") or f"https://arxiv.org/pdf/{arxiv_id}",
                    venue=clean_text(entry.findtext("arxiv:journal_ref", namespaces=ns)) or None,
                    source=self.id,
                    source_ids=source_ids,
                    categories=[node.attrib.get("term", "") for node in entry.findall("atom:category", ns)],
                    metadata={"comment": clean_text(entry.findtext("arxiv:comment", namespaces=ns))},
                )
            )
        return papers

