#!/usr/bin/env python3
"""Unit tests for the Zotero Review Pipeline."""

import json
import tempfile
import unittest
from pathlib import Path
import sys

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from zotero_review_pipeline import (
    ZoteroConnector,
    ZoteroItem,
    PDFExtractor,
    PaperCardExtractor,
    SCIReviewSynthesizer,
    ReferenceVerifier,
    run_pipeline,
)


class TestZoteroReviewPipeline(unittest.TestCase):
    def setUp(self):
        self.connector = ZoteroConnector()
        self.mock_items = self.connector.fetch_items_mock("Solid-State Batteries")

    def test_mock_items_structure(self):
        self.assertGreaterEqual(len(self.mock_items), 4)
        for item in self.mock_items:
            self.assertTrue(item.cite_key)
            self.assertTrue(item.title)
            self.assertTrue(item.doi)
            self.assertTrue(item.abstract)

    def test_paper_card_extraction(self):
        item = self.mock_items[0]
        card = PaperCardExtractor.create_card(item)
        self.assertEqual(card.cite_key, item.cite_key)
        self.assertIn("Chemo-mechanical", card.problem_statement)
        self.assertGreater(len(card.quantitative_findings), 0)
        self.assertTrue(card.proposed_mechanism)
        self.assertTrue(card.limitations_and_boundary)

    def test_evidence_matrix_generation(self):
        cards = [PaperCardExtractor.create_card(it) for it in self.mock_items]
        matrix_md = SCIReviewSynthesizer.generate_evidence_matrix_markdown(cards)
        self.assertIn("Cross-Study Evidence & Performance Matrix", matrix_md)
        for card in cards:
            self.assertIn(f"[{card.cite_key}]", matrix_md)

    def test_synthesize_review_manuscript(self):
        cards = [PaperCardExtractor.create_card(it) for it in self.mock_items]
        manuscript = SCIReviewSynthesizer.synthesize_review_manuscript(
            topic="Solid-State Lithium Batteries", cards=cards
        )
        self.assertIn("1. Introduction and Thematic Scope", manuscript)
        self.assertIn("2. Fundamental Mechanisms Governing Solid-Solid Interfaces", manuscript)
        self.assertIn("4. Synthesis of Controversies, Conflicting Results, and Research Gaps", manuscript)
        self.assertIn("5. Strategic Roadmap and Future Research Horizons", manuscript)

    def test_citation_verification_and_bibtex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            cards = [PaperCardExtractor.create_card(it) for it in self.mock_items]
            manuscript = SCIReviewSynthesizer.synthesize_review_manuscript(
                topic="Solid-State Lithium Batteries", cards=cards
            )

            bib_path = tmp_path / "references.bib"
            ReferenceVerifier.export_bibtex(self.mock_items, bib_path)
            self.assertTrue(bib_path.exists())

            bib_content = bib_path.read_text()
            for it in self.mock_items:
                self.assertIn(it.cite_key, bib_content)

            verification = ReferenceVerifier.verify_citations(manuscript, self.mock_items)
            self.assertTrue(verification["passed"])
            self.assertEqual(verification["unresolved_count"], 0)
            self.assertEqual(verification["verified_count"], len(self.mock_items))

    def test_end_to_end_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_pipeline(
                test_mode=True,
                query="Solid-State Lithium Batteries",
                output_dir=tmpdir
            )
            self.assertTrue(result["verification"]["passed"])
            self.assertTrue(Path(result["manuscript_path"]).exists())
            self.assertTrue(Path(result["bib_path"]).exists())


if __name__ == "__main__":
    unittest.main()
