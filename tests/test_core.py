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
from paper_radar.reading import (
    DeepReadItem,
    DeepReadRun,
    DeepReader,
    extract_one_sentence,
    local_pdf_canonical_id,
    paper_from_url,
    paper_storage_key,
    render_reading_index,
)
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

    @patch("paper_radar.delivery.shutil.which", return_value="/usr/bin/hermes")
    @patch("paper_radar.delivery.subprocess.run")
    def test_hermes_delivery_adds_attachments(self, run, _which) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as temporary:
            note = Path(temporary) / "note.md"
            note.write_text("deep read", encoding="utf-8")
            publish_with_hermes(self.config, "Ready", attachments=[note])
            body = run.call_args.kwargs["input"]
            self.assertIn("Ready", body)
            self.assertIn(f"MEDIA:{note.resolve()}", body)

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

    def test_manual_arxiv_url_resolves_pdf_and_key(self) -> None:
        paper = paper_from_url(
            "https://arxiv.org/abs/2608.12345",
            title="A Reliable Agent",
            topics=["agentic_systems"],
        )
        self.assertEqual(paper["canonical_id"], "arxiv:2608.12345")
        self.assertEqual(paper["pdf_url"], "https://arxiv.org/pdf/2608.12345")
        self.assertEqual(paper_storage_key(paper), "arxiv-2608.12345")

    def test_nature_pdf_candidate(self) -> None:
        paper = {
            "url": "https://www.nature.com/articles/s41591-026-04431-5?source=test",
            "source_ids": {},
        }
        candidates = DeepReader._pdf_candidates(paper)
        self.assertEqual(
            candidates,
            ["https://www.nature.com/articles/s41591-026-04431-5.pdf"],
        )

    def test_local_pdf_identity_uses_content_not_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first_dir = Path(temporary) / "one"
            second_dir = Path(temporary) / "two"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "paper.pdf"
            second = second_dir / "paper.pdf"
            first.write_bytes(b"%PDF-first")
            second.write_bytes(b"%PDF-second")
            self.assertNotEqual(local_pdf_canonical_id(first), local_pdf_canonical_id(second))

    def test_reading_index_and_one_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "library" / "papers" / "arxiv-1" / "deep-read.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "---\ntitle: Test\n---\n\n## 一句话总结\n\n这是核心结论。\n\n## 研究问题\n\n问题。\n",
                encoding="utf-8",
            )
            item = DeepReadItem(
                rank=1,
                canonical_id="arxiv:1",
                title="Test Paper",
                url="https://arxiv.org/abs/1",
                paper_key="arxiv-1",
                status="complete",
                evidence="full_text_pdf",
                note_path=str(note),
            )
            run = DeepReadRun(
                target_date="2026-08-27",
                generated_at="2026-08-27T08:00:00-04:00",
                input_path=str(root / "data" / "inbox" / "2026-08-27.json"),
                output_root=str(root),
                items=[item],
            )
            index = render_reading_index(run)
            self.assertIn("../../library/papers/arxiv-1/deep-read.md", index)
            self.assertEqual(extract_one_sentence(note), "这是核心结论。")


if __name__ == "__main__":
    unittest.main()
