#!/usr/bin/env python3
"""Unit tests for the ARTA-Compatible Zotero Literature Review Pipeline."""

import json
import tempfile
import unittest
from pathlib import Path
import sys

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from zotero_review_pipeline import (
    ZoteroLocalConnector,
    PaperItem,
    ThesisStudentInfo,
    PDFExtractor,
    PaperCardBuilder,
    ARTAThesisSynthesizer,
    run_arta_pipeline,
)


class TestARTAZoteroReviewPipeline(unittest.TestCase):
    def setUp(self):
        self.connector = ZoteroLocalConnector()
        self.mock_items = self.connector.fetch_items("鲜味肽机器学习筛选", test_mode=True)
        self.student = ThesisStudentInfo()

    def test_paper_items_structure(self):
        self.assertEqual(len(self.mock_items), 4)
        for item in self.mock_items:
            self.assertTrue(item.item_key)
            self.assertTrue(item.title)
            self.assertTrue(item.doi)
            self.assertTrue(item.csl_json)
            self.assertTrue(item.uri)

    def test_paper_card_builder(self):
        item = self.mock_items[0]
        card = PaperCardBuilder.build(item)
        self.assertEqual(card.item_key, item.item_key)
        self.assertIn("可解释性", card.problem_statement)
        self.assertGreater(len(card.quantitative_findings), 0)
        self.assertTrue(card.proposed_mechanism)
        self.assertTrue(card.limitations_and_boundary)

    def test_three_line_table_generation(self):
        cards = [PaperCardBuilder.build(it) for it in self.mock_items]
        table_md = ARTAThesisSynthesizer.generate_three_line_table_markdown(cards)
        self.assertIn("表 1-1", table_md)
        for card in cards:
            self.assertIn(f"[{card.item_key}]", table_md)

    def test_thesis_chapter1_synthesis(self):
        cards = [PaperCardBuilder.build(it) for it in self.mock_items]
        topic = "基于机器学习的食源性鲜味肽高通量筛选与呈味机制解析"
        chapter1_md = ARTAThesisSynthesizer.synthesize_thesis_chapter1(topic, self.student, cards)
        self.assertIn("# 第1章 绪论", chapter1_md)
        self.assertIn("## 1.1 研究背景与重大科研意义", chapter1_md)
        self.assertIn("## 1.2 食源性鲜味肽机器学习筛选模型研究进展", chapter1_md)
        self.assertIn("## 1.3 人体鲜味受体 T1R1/T1R3 互作结构与分子呈味机制", chapter1_md)
        self.assertIn("## 1.5 本文研究内容与章节架构", chapter1_md)

    def test_arta_dual_track_payload(self):
        cards = [PaperCardBuilder.build(it) for it in self.mock_items]
        topic = "基于机器学习的食源性鲜味肽高通量筛选与呈味机制解析"
        payload = ARTAThesisSynthesizer.generate_arta_synthesis_payload(topic, self.student, self.mock_items, cards)
        self.assertEqual(payload["project_metadata"]["topic"], topic)
        self.assertEqual(len(payload["csl_word_citations"]), 4)
        self.assertEqual(len(payload["literature_inventory"]), 4)
        for cite in payload["csl_word_citations"]:
            self.assertIn("citationID", cite)
            self.assertIn("citationItems", cite)

    def test_arta_ppt_deck_payload(self):
        cards = [PaperCardBuilder.build(it) for it in self.mock_items]
        topic = "基于机器学习的食源性鲜味肽高通量筛选与呈味机制解析"
        ppt_payload = ARTAThesisSynthesizer.generate_arta_ppt_deck_payload(topic, self.student, cards)
        self.assertEqual(len(ppt_payload["slides"]), 6)
        self.assertGreaterEqual(ppt_payload["deck_metadata"]["typography_rules"]["body_min_pt"], 18)
        self.assertGreaterEqual(ppt_payload["deck_metadata"]["typography_rules"]["title_pt"], 28)

    def test_end_to_end_arta_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_arta_pipeline(
                test_mode=True,
                topic="基于机器学习的食源性鲜味肽高通量筛选与呈味机制解析",
                output_dir=tmpdir
            )
            self.assertTrue(Path(result["chapter1_path"]).exists())
            self.assertTrue(Path(result["payload_path"]).exists())
            self.assertTrue(Path(result["ppt_path"]).exists())
            self.assertTrue(Path(result["bib_path"]).exists())
            self.assertTrue(Path(result["ris_path"]).exists())
            self.assertEqual(result["items_count"], 4)


if __name__ == "__main__":
    unittest.main()
