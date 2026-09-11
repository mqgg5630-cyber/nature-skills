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
        self.assertEqual(len(self.mock_items), 8)
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
        self.assertIn("滇中黄牛", card.problem_statement)
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
        self.assertIn("## 1.1 研究背景与重大战略需求", chapter1_md)
        self.assertIn("## 1.2 食源性基质多样性与酶解释放动力学", chapter1_md)
        self.assertIn("## 1.3 鲜味肽机器学习筛选模型与算法演进", chapter1_md)
        self.assertIn("## 1.4 人体鲜味受体 T1R1/T1R3 互作机制与分子构效解析", chapter1_md)
        self.assertIn("## 1.5 研究空白、产业转化挑战与本文工作", chapter1_md)

    def test_arta_dual_track_payload(self):
        cards = [PaperCardBuilder.build(it) for it in self.mock_items]
        topic = "基于机器学习的食源性鲜味肽高通量筛选与呈味机制解析"
        payload = ARTAThesisSynthesizer.generate_arta_synthesis_payload(topic, self.student, self.mock_items, cards)
        self.assertEqual(payload["project_metadata"]["topic"], topic)
        self.assertEqual(len(payload["csl_word_citations"]), 8)
        self.assertEqual(len(payload["literature_inventory"]), 8)
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
            self.assertEqual(result["paper_count"], 8)


if __name__ == "__main__":
    unittest.main()


class TestNoCrossTopicContamination(unittest.TestCase):
    """回归：非鲜味肽主题绝不能混入鲜味肽范文的写死内容。

    历史事故：batch_2「深度学习筛选抗菌肽」生成的绪论里出现了 T1R1/T1R3 受体机制、
    降盐 30%、小分子肽 84.5%、感官阈值 0.12 mg/mL 以及「陈峰/赵伟/王芳」等
    并不属于该批次的作者，全部由写死的鲜味肽范文注入。
    """

    UMAMI_LEAKS = ["T1R1", "T1R3", "鲜味", "降盐", "84.5%", "0.12 mg/mL",
                   "87.2%", "-8.65", "陈峰", "赵伟", "黄婷", "王芳", "吴浩", "张超"]

    def _cards(self, titles, prefix="CNKI_AMP"):
        items = [PaperItem(item_key=f"{prefix}{i:02d}", title=t, authors=["某作者"],
                           year="2025", journal="学位论文", doi="", abstract="")
                 for i, t in enumerate(titles, 1)]
        return [PaperCardBuilder.build(it) for it in items]

    def test_amp_topic_has_no_umami_content(self):
        cards = self._cards(["基于深度学习的抗菌肽识别及分类方法研究",
                             "基于迁移学习的植物抗菌肽及其功能预测研究"])
        md = ARTAThesisSynthesizer.synthesize_thesis_chapter1(
            "深度学习筛选抗菌肽", ThesisStudentInfo(), cards)
        for leak in self.UMAMI_LEAKS:
            self.assertNotIn(leak, md, f"非鲜味肽主题的综述里混入了 {leak!r}")
        self.assertIn("抗菌肽", md)

    def test_generic_chapter_marks_draft_and_pending(self):
        cards = self._cards(["基于深度学习的抗菌肽识别及分类方法研究"])
        md = ARTAThesisSynthesizer.synthesize_thesis_chapter1(
            "深度学习筛选抗菌肽", ThesisStudentInfo(), cards)
        self.assertIn("草稿", md)      # 必须自曝是草稿
        self.assertIn("待核", md)      # 抽取不到的必须标待核
        self.assertIn("参考文献", md)

    def test_references_only_from_this_batch(self):
        """参考文献必须与本批次 item_key 一一对应，不能出现别批次文献。"""
        cards = self._cards(["抗菌肽甲", "抗菌肽乙", "抗菌肽丙"])
        md = ARTAThesisSynthesizer.synthesize_thesis_chapter1(
            "深度学习筛选抗菌肽", ThesisStudentInfo(), cards)
        for c in cards:
            self.assertIn(c.item_key, md)
        for foreign in ("O8Y2Q3BF", "ICNTLPWH", "M65D587M", "SJJWK8PJ", "MU8NBLYB"):
            self.assertNotIn(foreign, md)

    def test_umami_topic_still_uses_domain_template(self):
        """鲜味肽主题的既有行为不能被破坏。"""
        items = [PaperItem(item_key="O8Y2Q3BF",
                           title="滇中黄牛新型鲜味肽的分离鉴定及与T1R1/T1R3受体的分子作用机制研究",
                           authors=["赵伟"], year="2026", journal="食品工业科技",
                           doi="", abstract="")]
        md = ARTAThesisSynthesizer.synthesize_thesis_chapter1(
            "食源性鲜味肽机器学习筛选", ThesisStudentInfo(), [PaperCardBuilder.build(i) for i in items])
        self.assertIn("T1R1", md)
        self.assertIn("鲜味", md)

    def test_unknown_item_fallback_never_fabricates(self):
        """未知条目的兜底卡片必须全部是「待核」，不能给出看似真实的结论。"""
        card = PaperCardBuilder.build(PaperItem(
            item_key="UNKNOWN_XYZ", title="某篇未收录文献", authors=[], year="2025",
            journal="", doi="", abstract=""))
        self.assertIn("待核", card.materials_methods)
        self.assertIn("待核", card.limitations_and_boundary)
        self.assertTrue(all("待核" in f for f in card.quantitative_findings))
        # 历史占位符必须消失
        self.assertNotIn("具有统计学显著性改善", " ".join(card.quantitative_findings))
        self.assertNotEqual(card.limitations_and_boundary, "特定实验体系限制。")
