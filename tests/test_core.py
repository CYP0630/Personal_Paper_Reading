from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from paper_radar.config import ResearchConfig
from paper_radar.dedupe import deduplicate
from paper_radar.delivery import DeliveryError, publish_with_hermes
from paper_radar.http import HttpClient
from paper_radar.models import Paper
from paper_radar.scoring import score_papers, select_digest
from paper_radar.sources.arxiv import ArxivSource
from paper_radar.sources.base import FetchContext
from paper_radar.sources.huggingface import HuggingFaceSource
from paper_radar.sources.nature import NatureSource
from paper_radar.sources.openreview import OpenReviewSource
from paper_radar.sources.pubmed import PubMedSource
from paper_radar.render import render_discord


ROOT = Path(__file__).resolve().parents[1]
SINCE = datetime(2026, 8, 20, tzinfo=timezone.utc)
UNTIL = datetime(2026, 8, 27, tzinfo=timezone.utc)


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = ResearchConfig.load(ROOT / "config" / "topics.yaml")

    def context(self) -> FetchContext:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return FetchContext(
            config=self.config,
            http=HttpClient(Path(temporary.name), offline=True),
            since=SINCE,
            until=UNTIL,
            limit=50,
        )

    def test_deduplicate_merges_provenance(self) -> None:
        arxiv = Paper(
            canonical_id="arxiv:2608.00001",
            title="A Tool-Using Agent for Science",
            source="arxiv",
            url="https://arxiv.org/abs/2608.00001",
            abstract="Long abstract from arXiv.",
            source_ids={"arxiv": "2608.00001"},
        )
        hf = Paper(
            canonical_id="arxiv:2608.00001",
            title="A Tool-Using Agent for Science",
            source="huggingface_papers",
            url="https://huggingface.co/papers/2608.00001",
            source_ids={"arxiv": "2608.00001"},
            metadata={"hf_upvotes": 12},
        )
        unique = deduplicate([arxiv, hf])
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].discovered_by, ["arxiv", "huggingface_papers"])
        self.assertEqual(unique[0].metadata["hf_upvotes"], 12)

    def test_scoring_and_selection(self) -> None:
        paper = Paper(
            canonical_id="arxiv:2608.00002",
            title="Self-Evolving LLM Agents Learn Tool Use with Verifiable Rewards",
            abstract="A language model agent improves long-horizon tool use with RLVR and agent memory.",
            source="arxiv",
            url="https://arxiv.org/abs/2608.00002",
            published_at="2026-08-26T00:00:00Z",
            categories=["cs.AI", "cs.CL"],
        )
        score_papers([paper], self.config, now=UNTIL)
        self.assertIn("agentic_systems", paper.topics)
        self.assertIn("post_training", paper.topics)
        self.assertGreaterEqual(paper.scores["fit"], 0.55)
        self.assertEqual(select_digest([paper], self.config, size=8), [paper])

    def test_arxiv_parser(self) -> None:
        xml = b"""<?xml version='1.0'?>
        <feed xmlns='http://www.w3.org/2005/Atom' xmlns:arxiv='http://arxiv.org/schemas/atom'>
          <entry>
            <id>http://arxiv.org/abs/2608.00003v1</id>
            <updated>2026-08-25T12:00:00Z</updated><published>2026-08-25T12:00:00Z</published>
            <title>Agent Memory at Scale</title><summary>An LLM agent memory system.</summary>
            <author><name>A. Researcher</name></author>
            <link href='https://arxiv.org/abs/2608.00003' rel='alternate'/>
            <link title='pdf' href='https://arxiv.org/pdf/2608.00003'/>
            <category term='cs.AI'/>
          </entry>
        </feed>"""
        papers = ArxivSource().parse(xml, self.context())
        self.assertEqual(papers[0].source_ids["arxiv"], "2608.00003")
        self.assertEqual(papers[0].categories, ["cs.AI"])

    def test_huggingface_parser(self) -> None:
        payload = [
            {
                "publishedAt": "2026-08-25T10:00:00Z",
                "upvotes": 17,
                "paper": {
                    "id": "2608.00004",
                    "title": "A Multimodal Search Agent",
                    "summary": "An agent for video and audio search.",
                    "authors": [{"name": "B. Researcher"}],
                    "githubRepo": "https://github.com/example/repo",
                },
            }
        ]
        papers = HuggingFaceSource().parse(payload, self.context())
        self.assertEqual(papers[0].metadata["hf_upvotes"], 17)
        self.assertEqual(papers[0].source_ids["arxiv"], "2608.00004")

    def test_nature_parser(self) -> None:
        xml = b"""<rss version='2.0' xmlns:dc='http://purl.org/dc/elements/1.1/'>
          <channel><item><title>Clinical agent evaluation</title>
          <link>https://www.nature.com/articles/s41591-026-09999-9</link>
          <guid>https://doi.org/10.1038/s41591-026-09999-9</guid>
          <description><![CDATA[<p>A medical LLM evaluation.</p>]]></description>
          <pubDate>Tue, 25 Aug 2026 10:00:00 GMT</pubDate>
          <dc:creator>C. Researcher</dc:creator></item></channel></rss>"""
        journal = {"id": "nature_medicine", "name": "Nature Medicine", "score_boost": 0.25}
        papers = NatureSource().parse(xml, self.context(), journal)
        self.assertEqual(papers[0].source_ids["doi"], "10.1038/s41591-026-09999-9")
        self.assertEqual(papers[0].venue, "Nature Medicine")

    def test_openreview_parser(self) -> None:
        payload = {
            "notes": [
                {
                    "id": "abc123",
                    "cdate": 1787652000000,
                    "mdate": 1787652000000,
                    "content": {
                        "title": {"value": "Evidence-Based Medical Agent"},
                        "abstract": {"value": "A clinical LLM agent."},
                        "authors": {"value": ["D. Researcher"]},
                        "venue": {"value": "ICLR 2027 Submission"},
                    },
                }
            ]
        }
        papers = OpenReviewSource().parse(payload, self.context())
        self.assertEqual(papers[0].canonical_id, "openreview:abc123")
        self.assertEqual(papers[0].authors, ["D. Researcher"])

    def test_pubmed_parser(self) -> None:
        xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation>
          <PMID>99999999</PMID><Article><ArticleTitle>Medical LLM Agent</ArticleTitle>
          <Abstract><AbstractText Label='BACKGROUND'>Clinical reasoning.</AbstractText></Abstract>
          <AuthorList><Author><ForeName>E.</ForeName><LastName>Researcher</LastName></Author></AuthorList>
          <Journal><JournalIssue><PubDate><Year>2026</Year><Month>Aug</Month><Day>25</Day></PubDate></JournalIssue>
          <Title>Nature Medicine</Title></Journal></Article></MedlineCitation>
          <PubmedData><History><PubMedPubDate PubStatus='entrez'><Year>2026</Year><Month>8</Month><Day>25</Day></PubMedPubDate></History>
          <ArticleIdList><ArticleId IdType='doi'>10.1038/example</ArticleId></ArticleIdList></PubmedData>
          </PubmedArticle></PubmedArticleSet>"""
        papers = PubMedSource().parse(xml, self.context())
        self.assertEqual(papers[0].canonical_id, "doi:10.1038/example")
        self.assertIn("BACKGROUND", papers[0].abstract)

    @patch("paper_radar.delivery.shutil.which", return_value="/usr/bin/hermes")
    @patch("paper_radar.delivery.subprocess.run")
    def test_hermes_delivery(self, run, _which) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        result = publish_with_hermes(self.config, "Daily digest")
        self.assertEqual(result.target, "discord:1542379320289263676")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["/usr/bin/hermes", "send", "--to", result.target])
        self.assertEqual(run.call_args.kwargs["input"], "Daily digest")

    @patch("paper_radar.delivery.shutil.which", return_value=None)
    def test_hermes_delivery_requires_executable(self, _which) -> None:
        with self.assertRaises(DeliveryError):
            publish_with_hermes(self.config, "Daily digest")

    def test_discord_render_is_compact(self) -> None:
        paper = Paper(
            canonical_id="arxiv:2608.00100",
            title="Agentic Paper",
            source="arxiv",
            url="https://arxiv.org/abs/2608.00100",
            topics=["agentic_systems"],
            scores={"fit": 0.9, "heat": 0.7},
            reasons=["匹配 agentic_systems"],
        )
        from paper_radar.pipeline import DiscoveryResult

        result = DiscoveryResult(
            generated_at="2026-08-26T00:00:00Z",
            window_start="2026-08-22T00:00:00Z",
            window_end="2026-08-26T00:00:00Z",
            fetched_count=1,
            unique_count=1,
            eligible_count=1,
            selected=[paper],
        )
        message = render_discord(result, target_date="2026-08-26")
        self.assertIn("Top 1", message)
        self.assertIn("Agentic Paper", message)
        self.assertNotIn("abstract", message.lower())


if __name__ == "__main__":
    unittest.main()
