from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from paper_radar.models import Paper
from paper_radar.sources.base import FetchContext
from paper_radar.utils import clean_text, in_window, isoformat, parse_datetime


class PubMedSource:
    id = "pubmed"
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    doi_pattern = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)

    def fetch(self, context: FetchContext) -> list[Paper]:
        medical = context.config.topic_by_id.get("agentic_medicine", {})
        terms = [str(term) for term in medical.get("include_terms", [])[:18]]
        query = " OR ".join(f'"{term}"[Title/Abstract]' for term in terms)
        common: dict[str, str | int] = {
            "db": "pubmed",
            "tool": "personal_paper_radar",
        }
        email = os.environ.get("PAPER_RADAR_EMAIL", "").strip()
        api_key = os.environ.get("NCBI_API_KEY", "").strip()
        if email:
            common["email"] = email
        if api_key:
            common["api_key"] = api_key

        search_params = {
            **common,
            "term": query,
            "retmode": "json",
            "retmax": min(context.limit, 300),
            "sort": "pub_date",
            "datetype": "edat",
            "mindate": context.since.strftime("%Y/%m/%d"),
            "maxdate": context.until.strftime("%Y/%m/%d"),
        }
        search_body = context.http.get(
            f"{self.base_url}/esearch.fcgi",
            params=search_params,
            ttl_seconds=1800,
        )
        payload = context.http.json(search_body)
        if not isinstance(payload, dict):
            return []
        result = payload.get("esearchresult")
        ids = result.get("idlist", []) if isinstance(result, dict) else []
        if not ids:
            return []

        fetch_body = context.http.get(
            f"{self.base_url}/efetch.fcgi",
            params={
                **common,
                "db": "pubmed",
                "id": ",".join(str(identifier) for identifier in ids),
                "retmode": "xml",
            },
            ttl_seconds=1800,
        )
        return self.parse(fetch_body, context)

    def parse(self, body: bytes, context: FetchContext) -> list[Paper]:
        root = ET.fromstring(body)
        papers: list[Paper] = []
        for article in root.findall(".//PubmedArticle"):
            citation = article.find("MedlineCitation")
            journal_article = citation.find("Article") if citation is not None else None
            if citation is None or journal_article is None:
                continue
            pmid = clean_text(citation.findtext("PMID"))
            title_node = journal_article.find("ArticleTitle")
            title = clean_text("".join(title_node.itertext()) if title_node is not None else "")
            if not pmid or not title:
                continue
            published = self._publication_date(article)
            if not in_window(published, context.since, context.until):
                continue
            abstract_parts: list[str] = []
            for node in journal_article.findall("Abstract/AbstractText"):
                label = node.attrib.get("Label", "")
                text = clean_text("".join(node.itertext()))
                abstract_parts.append(f"{label}: {text}" if label else text)
            authors: list[str] = []
            for author in journal_article.findall("AuthorList/Author"):
                collective = clean_text(author.findtext("CollectiveName"))
                personal = clean_text(
                    " ".join(filter(None, [author.findtext("ForeName"), author.findtext("LastName")]))
                )
                if collective or personal:
                    authors.append(collective or personal)
            identifiers = {
                clean_text(node.attrib.get("IdType")): clean_text(node.text)
                for node in article.findall("PubmedData/ArticleIdList/ArticleId")
            }
            doi = identifiers.get("doi", "").lower()
            source_ids = {"pubmed": pmid}
            if doi:
                source_ids["doi"] = doi
            journal = clean_text(
                journal_article.findtext("Journal/Title")
                or journal_article.findtext("Journal/ISOAbbreviation")
            )
            papers.append(
                Paper(
                    canonical_id=f"doi:{doi}" if doi else f"pubmed:{pmid}",
                    title=title,
                    abstract=" ".join(abstract_parts),
                    authors=authors,
                    published_at=isoformat(published),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    venue=journal or None,
                    source=self.id,
                    source_ids=source_ids,
                    categories=[clean_text(node.text) for node in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")],
                    metadata={"pmc": identifiers.get("pmc", "")},
                )
            )
        return papers

    @staticmethod
    def _publication_date(article: ET.Element) -> datetime | None:
        completed = article.find("PubmedData/History/PubMedPubDate[@PubStatus='entrez']")
        publication = article.find("MedlineCitation/Article/Journal/JournalIssue/PubDate")
        node = completed if completed is not None else publication
        if node is None:
            return None
        year = clean_text(node.findtext("Year"))
        month = clean_text(node.findtext("Month")) or "1"
        day = clean_text(node.findtext("Day")) or "1"
        if not year:
            medline = clean_text(node.findtext("MedlineDate"))
            match = re.search(r"(19|20)\d{2}", medline)
            year = match.group(0) if match else ""
        if not year:
            return parse_datetime(None)
        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        month_number = month_map.get(month[:3].lower(), int(month) if month.isdigit() else 1)
        try:
            return datetime(int(year), month_number, int(day), tzinfo=timezone.utc)
        except ValueError:
            return datetime(int(year), month_number, 1, tzinfo=timezone.utc)
