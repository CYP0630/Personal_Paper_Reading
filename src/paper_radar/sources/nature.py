from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from paper_radar.models import Paper
from paper_radar.sources.base import FetchContext
from paper_radar.utils import clean_text, html_to_text, in_window, isoformat, parse_datetime


class NatureSource:
    id = "nature"
    default_feeds = {
        "nature": "https://www.nature.com/nature.rss",
        "nature_medicine": "https://www.nature.com/nm.rss",
        "nature_machine_intelligence": "https://www.nature.com/natmachintell.rss",
    }
    doi_pattern = re.compile(r"10\.1038/[A-Za-z0-9._;()/:-]+", re.IGNORECASE)

    def fetch(self, context: FetchContext) -> list[Paper]:
        papers: list[Paper] = []
        per_feed = max(10, context.limit // max(1, len(context.config.journal_watch)))
        for journal in context.config.journal_watch:
            feed_url = journal.get("feed_url") or self.default_feeds.get(journal["id"])
            if not feed_url:
                continue
            body = context.http.get(feed_url, ttl_seconds=1800)
            papers.extend(self.parse(body, context, journal)[:per_feed])
        return papers[: context.limit]

    def parse(
        self,
        body: bytes,
        context: FetchContext,
        journal: dict[str, object],
    ) -> list[Paper]:
        root = ET.fromstring(body)
        papers: list[Paper] = []
        rss = "http://purl.org/rss/1.0/"
        dc = "http://purl.org/dc/elements/1.1/"
        prism = "http://prismstandard.org/namespaces/basic/2.0/"
        content = "http://purl.org/rss/1.0/modules/content/"
        rdf = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        items = [*root.findall(".//item"), *root.findall(f".//{{{rss}}}item")]
        for item in items:
            title = html_to_text(item.findtext("title") or item.findtext(f"{{{rss}}}title"))
            url = clean_text(item.findtext("link") or item.findtext(f"{{{rss}}}link"))
            description = html_to_text(
                item.findtext("description")
                or item.findtext(f"{{{rss}}}description")
                or item.findtext(f"{{{content}}}encoded")
            )
            published = parse_datetime(
                item.findtext("pubDate")
                or item.findtext(f"{{{rss}}}pubDate")
                or item.findtext(f"{{{dc}}}date")
            )
            if not title or not url or not in_window(published, context.since, context.until):
                continue
            identifiers = [
                clean_text(item.findtext("guid")),
                clean_text(item.findtext(f"{{{rss}}}guid")),
                clean_text(item.findtext(f"{{{dc}}}identifier")),
                clean_text(item.findtext(f"{{{prism}}}doi")),
                clean_text(item.attrib.get(f"{{{rdf}}}about")),
                url,
            ]
            doi = ""
            for identifier in identifiers:
                match = self.doi_pattern.search(identifier)
                if match:
                    doi = match.group(0).rstrip(".,)").lower()
                    break
            journal_id = str(journal["id"])
            canonical_id = f"doi:{doi}" if doi else f"nature:{journal_id}:{url.rsplit('/', 1)[-1]}"
            source_ids = {"doi": doi} if doi else {"nature": url.rsplit("/", 1)[-1]}
            authors = [
                clean_text(node.text)
                for node in item.findall(f"{{{dc}}}creator")
                if clean_text(node.text)
            ]
            papers.append(
                Paper(
                    canonical_id=canonical_id,
                    title=title,
                    abstract=description,
                    authors=authors,
                    published_at=isoformat(published),
                    url=url,
                    source=f"nature:{journal_id}",
                    source_ids=source_ids,
                    venue=str(journal.get("name", journal_id)),
                    metadata={
                        "journal_id": journal_id,
                        "journal_score_boost": float(journal.get("score_boost", 0.0)),
                    },
                )
            )
        return papers
