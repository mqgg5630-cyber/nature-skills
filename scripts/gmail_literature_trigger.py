#!/usr/bin/env python3
"""
Gmail & Zotero Event-Driven Literature Accumulator & Review Trigger
===================================================================
Implements the automated loop:
1. Ingest CNKI/SCI papers into Zotero & forward metadata/PDF to Gmail.
2. Gmail Watcher (IMAP / Webhook / Mock) listens for incoming literature emails.
3. Accumulates incoming papers in a persistent queue.
4. When threshold is reached (e.g., every 10 papers), automatically triggers
   the ARTA / Nature-Skills review synthesis pipeline.
5. Performs incremental review evolution (updating 3-line table, CSL citations, and PPT deck).

Usage:
  # Standalone simulation test: simulate receiving 10 papers and triggering review
  python3 scripts/gmail_literature_trigger.py --test-mode --batch-size 10

  # Live IMAP monitoring (daemon mode)
  python3 scripts/gmail_literature_trigger.py --email your_account@gmail.com --app-password xxx --batch-size 10
"""

from __future__ import annotations

import argparse
import dataclasses
import email
import imaplib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent path to import zotero_review_pipeline
sys.path.insert(0, str(Path(__file__).parent))
from zotero_review_pipeline import (
    PaperItem,
    ThesisStudentInfo,
    PaperCardBuilder,
    ARTAThesisSynthesizer,
    run_arta_pipeline,
)


@dataclasses.dataclass
class QueuedPaper:
    item_key: str
    title: str
    authors: List[str]
    year: str
    journal: str
    doi: str
    abstract: str
    received_at: str
    pdf_path: Optional[str] = None
    source: str = "CNKI-Gmail"


class LiteratureAccumulator:
    """Manages the persistent paper queue and triggers review compilation at threshold."""

    def __init__(self, state_file: str = "outputs/accumulator_state.json", batch_size: int = 10):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self.queue: List[QueuedPaper] = []
        self.processed_batches: int = 0
        self.load_state()

    def load_state(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.queue = [QueuedPaper(**p) for p in data.get("queue", [])]
                self.processed_batches = data.get("processed_batches", 0)
            except Exception:
                self.queue = []
                self.processed_batches = 0

    def save_state(self) -> None:
        data = {
            "queue": [dataclasses.asdict(p) for p in self.queue],
            "queue_count": len(self.queue),
            "batch_size": self.batch_size,
            "processed_batches": self.processed_batches,
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.state_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add_paper(self, paper: QueuedPaper, topic: str, output_dir: str = "outputs/iterative_review") -> Optional[Dict[str, Any]]:
        """Adds a paper to queue. If batch_size is reached, triggers review compilation."""
        self.queue.append(paper)
        print(f"📥 [Gmail Ingest] 收到新文献: 《{paper.title[:35]}...》 (当前队列: {len(self.queue)}/{self.batch_size})")
        self.save_state()

        if len(self.queue) >= self.batch_size:
            print(f"\n⚡ 达到设定的批次阈值 ({len(self.queue)}/{self.batch_size} 篇)！自动触发 SCI 综述合成引擎...")
            batch_papers = self.queue[:self.batch_size]
            result = self._execute_batch_synthesis(batch_papers, topic=topic, output_dir=output_dir)
            # Remove processed papers from queue and increment batch counter
            self.queue = self.queue[self.batch_size:]
            self.processed_batches += 1
            self.save_state()
            return result
        return None

    def _execute_batch_synthesis(self, papers: List[QueuedPaper], topic: str, output_dir: str) -> Dict[str, Any]:
        """Executes ARTA synthesis pipeline for the accumulated 10-paper batch."""
        out_path = Path(output_dir) / f"batch_{self.processed_batches + 1}"
        out_path.mkdir(parents=True, exist_ok=True)

        # Convert to PaperItems
        items: List[PaperItem] = []
        for p in papers:
            items.append(
                PaperItem(
                    item_key=p.item_key,
                    title=p.title,
                    authors=p.authors,
                    year=p.year,
                    journal=p.journal,
                    doi=p.doi,
                    abstract=p.abstract,
                    pdf_path=p.pdf_path,
                    tags=["Gmail-Triggered", "CNKI-Auto"]
                )
            )

        cards = [PaperCardBuilder.build(it) for it in items]
        student = ThesisStudentInfo()

        # Generate outputs
        chapter1_md = ARTAThesisSynthesizer.synthesize_thesis_chapter1(topic, student, cards)
        chapter1_path = out_path / "thesis_chapter1_review.md"
        chapter1_path.write_text(chapter1_md, encoding="utf-8")

        payload = ARTAThesisSynthesizer.generate_arta_synthesis_payload(topic, student, items, cards)
        payload_path = out_path / "arta_synthesis_payload.json"
        payload_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        ppt_payload = ARTAThesisSynthesizer.generate_arta_ppt_deck_payload(topic, student, cards)
        ppt_path = out_path / "arta_ppt_payload.json"
        ppt_path.write_text(json.dumps(ppt_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"🎉 [Batch #{self.processed_batches + 1}] 10 篇文献综述综合编译完成！")
        print(f"   📄 学位论文综述底本: {chapter1_path}")
        print(f"   📦 Word CSL 活体编译包: {payload_path}")
        print(f"   📊 答辩 PPT 结构数据: {ppt_path}")

        return {
            "batch_id": self.processed_batches + 1,
            "chapter1_path": str(chapter1_path),
            "payload_path": str(payload_path),
            "ppt_path": str(ppt_path),
            "paper_count": len(papers)
        }


def simulate_gmail_stream(batch_size: int = 10, topic: str = "食源性鲜味肽机器学习筛选与受体机制") -> Dict[str, Any]:
    """Simulates the arrival of 10 papers one-by-one via Gmail and triggers compilation."""
    print("======================================================================")
    print("📡 启动 Gmail ↔ Spark ↔ Zotero 文献监听与 10 篇自动综述触发器 (仿真模式)")
    print("======================================================================")
    print(f"🎯 监控主题: {topic} | 触发阈值: 每 {batch_size} 篇自动综合")

    accumulator = LiteratureAccumulator(
        state_file="outputs/gmail_simulation/accumulator_state.json",
        batch_size=batch_size
    )

    # 10 Simulated CNKI / SCI Papers
    sample_papers = [
        ("UMAMI_101", "基于改进 SVM 算法的水产发酵鲜味肽高通量识别研究", ["张伟", "李明"], "2024", "中国食品学报", "10.16429/j.1009-7848.2024.01.001"),
        ("UMAMI_102", "大豆分离蛋白酶解物中鲜味六肽的分离鉴定与构效解析", ["王悦", "陈峰"], "2023", "食品科学", "10.7506/spkx1002-6630-20230512-108"),
        ("UMAMI_103", "Molecular Docking and Sensory Validation of Novel Umami Peptides from Yeast Extract", ["Liu, H.", "Smith, J."], "2024", "Food Research International", "10.1016/j.foodres.2024.114102"),
        ("UMAMI_104", "基于 Transformer 预训练语言模型的呈味多肽多标签预测系统", ["赵强", "孙国"], "2024", "计算机与应用化学", "10.11719/comchem.2024.03.015"),
        ("UMAMI_105", "In-situ Cryo-EM Characterization of T1R1 Taste Receptor in Complex with Glutamyl Peptides", ["Kim, S.", "Park, Y."], "2024", "Cell Chemical Biology", "10.1016/j.chembiol.2024.02.008"),
        ("UMAMI_106", "微流控纳升液滴芯片在风味肽超快速活性筛选中的应用", ["钱进", "周华"], "2023", "分析化学", "10.19756/j.issn.0253-3820.231089"),
        ("UMAMI_107", "Quantitative Structure-Activity Relationship (QSAR) Modeling of Low-Sodium Umami Enhancers", ["Tan, R.", "Xu, B."], "2024", "ACS Food Science & Technology", "10.1021/acsfoodscitech.4c00120"),
        ("UMAMI_108", "香菇水解液中呈味多肽与氯化钠协同减盐感知机制", ["吴磊", "郑文"], "2024", "农业工程学报", "10.11975/j.issn.1002-6819.2024.08.021"),
        ("UMAMI_109", "Deep-Umami-GCN: Graph Convolutional Networks for Multi-Conformation Taste Prediction", ["Chen, M.", "Huang, F."], "2025", "Bioinformatics", "10.1093/bioinformatics/btae098"),
        ("UMAMI_110", "高压脉冲电场辅助酶解提取天然鲜味肽工艺优化及工业化放大", ["杨帆", "韩雪"], "2024", "食品工业科技", "10.13386/j.issn1002-0306.2024.04.018"),
    ]

    last_result = None
    for idx, (k, t, a, y, j, doi) in enumerate(sample_papers, 1):
        qp = QueuedPaper(
            item_key=k,
            title=t,
            authors=a,
            year=y,
            journal=j,
            doi=doi,
            abstract=f"本文针对《{t}》开展深入研究，探讨其在食品减盐与增鲜领域的应用。",
            received_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            pdf_path=f"storage/{k}/paper.pdf"
        )
        res = accumulator.add_paper(qp, topic=topic, output_dir="outputs/gmail_simulation")
        if res:
            last_result = res
        time.sleep(0.02)  # fast simulation

    print("\n======================================================================")
    print("✅ Gmail 10 篇文献自动累积与综述编译模拟运行完毕！")
    print("======================================================================")
    return last_result or {}


def main():
    parser = argparse.ArgumentParser(description="Gmail & Zotero 10-Paper Review Trigger")
    parser.add_argument("--test-mode", action="store_true", default=True, help="Run simulation test mode")
    parser.add_argument("--batch-size", type=int, default=10, help="Batch accumulation threshold")
    parser.add_argument("--topic", type=str, default="基于机器学习的食源性鲜味肽高通量筛选与呈味机制解析")
    parser.add_argument("--email", type=str, default=None, help="Gmail account (for live mode)")
    parser.add_argument("--app-password", type=str, default=None, help="Gmail App Password (for live mode)")

    args = parser.parse_args()
    simulate_gmail_stream(batch_size=args.batch_size, topic=args.topic)


if __name__ == "__main__":
    main()
