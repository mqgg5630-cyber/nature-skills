#!/usr/bin/env python3
"""Unit tests for the Gmail ↔ CNKI Literature Bridge (offline, no network)."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from gmail_cnki_bridge import (  # noqa: E402
    INGEST_PREFIX,
    GmailConfig,
    build_digest_message,
    build_ingest_message,
    find_local_pdf,
    is_cnki_entry,
    normalize_entries,
    parse_cnki_entries_from_message,
    process_ingest_message,
)
from gmail_literature_trigger import LiteratureAccumulator  # noqa: E402

CFG = GmailConfig(email="bridge-test@example.org", app_password="")

SAMPLE_CNKI = {
    "item_key": "CNKI_101",
    "title": "基于深度学习的鲜味肽高通量筛选方法研究",
    "authors": ["张三", "李四"],
    "year": "2025",
    "journal": "食品科学",
    "doi": "10.1234/spkx.2025.001",
    "cnki_url": "https://kns.cnki.net/kcms/detail/detail.aspx?filename=SPKX2025001&dbcode=CJFD",
    "abstract": "本文提出一种鲜味肽筛选模型。",
    "source": "cnki",
}

SAMPLE_ENGLISH = {
    "item_key": "ENG_901",
    "title": "Machine Learning for Peptide Screening",
    "authors": ["Smith J."],
    "year": "2025",
    "journal": "Food Research International",
    "doi": "10.1016/j.foodres.2025.001",
    "cnki_url": "",
    "abstract": "English paper.",
    "source": "sciencedirect",
}

FAKE_PDF = b"%PDF-1.5\nfake cnki paper content\n"


def _pdf_message(entry, cfg=CFG):
    msg = build_ingest_message(entry, FAKE_PDF, cfg)
    assert msg["Subject"].startswith(INGEST_PREFIX)
    return msg


class TestCnkiFilter(unittest.TestCase):
    def test_keeps_chinese_and_cnki_source(self):
        self.assertTrue(is_cnki_entry(dict(SAMPLE_CNKI)))

    def test_drops_english_entry(self):
        self.assertFalse(is_cnki_entry(dict(SAMPLE_ENGLISH)))

    def test_cnki_url_hits_even_without_cjk_title(self):
        entry = dict(SAMPLE_ENGLISH)
        entry["cnki_url"] = "https://kns.cnki.net/kcms2/article/xxx"
        self.assertTrue(is_cnki_entry(entry))

    def test_normalize_accepts_manifest_dict_and_string_authors(self):
        data = {"papers": [dict(SAMPLE_CNKI, authors="张三, 李四; 王五")]}
        entries = normalize_entries(data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["authors"], ["张三", "李四", "王五"])

    def test_normalize_generates_item_key(self):
        entries = normalize_entries([{"title": "无键标题测试"}])
        self.assertTrue(entries[0]["item_key"].startswith("CNKI_"))


class TestDigestRoundTrip(unittest.TestCase):
    def test_roundtrip_preserves_entries(self):
        entries = [dict(SAMPLE_CNKI)]
        msg = build_digest_message(entries, CFG, topic="测试主题")
        parsed = parse_cnki_entries_from_message(msg)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], SAMPLE_CNKI["title"])
        self.assertEqual(parsed[0]["item_key"], SAMPLE_CNKI["item_key"])
        self.assertEqual(parsed[0]["doi"], SAMPLE_CNKI["doi"])

    def test_roundtrip_without_attachment_uses_body_marker(self):
        entries = [dict(SAMPLE_CNKI)]
        msg = build_digest_message(entries, CFG, topic="测试主题")
        # 重建一个只有正文（含标记块）的邮件，验证正文标记兜底解析
        from email.message import EmailMessage
        body = msg.get_body("plain").get_content()
        plain = EmailMessage()
        plain["Subject"] = msg["Subject"]
        plain.set_content(body)
        parsed = parse_cnki_entries_from_message(plain)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["item_key"], SAMPLE_CNKI["item_key"])


class TestIngestProcessing(unittest.TestCase):
    def _run(self, tmpdir, batch_size=2, entries=None, messages=None):
        library = Path(tmpdir) / "library"
        accumulator = LiteratureAccumulator(
            state_file=str(library / "accumulator_state.json"), batch_size=batch_size
        )
        if messages is None:
            messages = [_pdf_message(e) for e in (entries or [dict(SAMPLE_CNKI)])]
        results = [process_ingest_message(m, library, accumulator, "测试主题") for m in messages]
        return library, accumulator, results

    def test_ingest_saves_pdf_and_queues(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            library, accumulator, results = self._run(tmpdir)
            self.assertEqual(results[0]["status"], "ingested")
            saved = Path(results[0]["saved_path"])
            self.assertTrue(saved.exists())
            self.assertEqual(saved.read_bytes(), FAKE_PDF)
            self.assertEqual(len(accumulator.queue), 1)
            index = json.loads((library / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index), 1)

    def test_threshold_triggers_review_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                dict(SAMPLE_CNKI, item_key="CNKI_A", title="综述测试文献甲"),
                dict(SAMPLE_CNKI, item_key="CNKI_B", title="综述测试文献乙"),
            ]
            # 两篇用不同的 PDF 字节，避免被 SHA-256 去重
            messages = [
                build_ingest_message(e, FAKE_PDF + e["item_key"].encode("utf-8"), CFG)
                for e in entries
            ]
            library, accumulator, results = self._run(tmpdir, batch_size=2, messages=messages)
            self.assertFalse(results[0]["review_triggered"])
            self.assertTrue(results[1]["review_triggered"])
            self.assertEqual(accumulator.processed_batches, 1)
            batch = library / "reviews" / "batch_1"
            self.assertTrue((batch / "thesis_chapter1_review.md").exists())
            self.assertTrue((batch / "arta_synthesis_payload.json").exists())

    def test_duplicate_pdf_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _, _, first = self._run(tmpdir, batch_size=99)
            self.assertEqual(first[0]["status"], "ingested")
            library = Path(tmpdir) / "library"
            accumulator = LiteratureAccumulator(
                state_file=str(library / "accumulator_state.json"), batch_size=99
            )
            second = process_ingest_message(_pdf_message(dict(SAMPLE_CNKI)), library, accumulator, "测试主题")
            self.assertEqual(second["status"], "duplicate")
            self.assertEqual(second["sha256"], hashlib.sha256(FAKE_PDF).hexdigest())

    def test_message_without_pdf_attachment_is_skipped(self):
        from email.message import EmailMessage
        with tempfile.TemporaryDirectory() as tmpdir:
            from gmail_cnki_bridge import _json_block
            msg = EmailMessage()
            msg["Subject"] = f"{INGEST_PREFIX} CNKI_101 | 无附件测试"
            msg.set_content("没有 PDF 附件的正文\n" + _json_block([dict(SAMPLE_CNKI)]))
            _, accumulator, results = self._run(tmpdir, batch_size=99, messages=[msg])
            self.assertEqual(results[0]["status"], "skipped")
            self.assertFalse(accumulator.queue)


class TestLocalPdfMatching(unittest.TestCase):
    def test_match_by_item_key_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "CNKI_101_xxx.pdf").write_bytes(FAKE_PDF)
            (d / "CNKI_102_yyy.pdf").write_bytes(FAKE_PDF)
            hit = find_local_pdf(d, dict(SAMPLE_CNKI))
            self.assertIsNotNone(hit)
            self.assertIn("CNKI_101", hit.name)

    def test_match_by_title_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "基于深度学习的鲜味肽高通量筛选方法研究_2025.pdf").write_bytes(FAKE_PDF)
            hit = find_local_pdf(d, dict(SAMPLE_CNKI))
            self.assertIsNotNone(hit)

    def test_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "unrelated.pdf").write_bytes(FAKE_PDF)
            self.assertIsNone(find_local_pdf(d, dict(SAMPLE_CNKI)))


if __name__ == "__main__":
    unittest.main()


class TestDoctorAndSimulationFlags(unittest.TestCase):
    """doctor 子命令与仿真数据标记的回归测试。"""

    def test_doctor_reports_missing_credentials(self):
        import argparse as _argparse
        import io
        import contextlib
        import gmail_cnki_bridge as bridge

        args = _argparse.Namespace(node="node", skill_dir=None, check_network=False)
        cfg = bridge.GmailConfig(email="", app_password="")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                bridge.cmd_doctor(args, cfg)
        self.assertEqual(ctx.exception.code, 1)
        out = buf.getvalue()
        self.assertIn("GMAIL_EMAIL", out)
        self.assertIn("DOCTOR", out)

    def test_doctor_passes_with_credentials(self):
        import argparse as _argparse
        import io
        import contextlib
        import gmail_cnki_bridge as bridge

        args = _argparse.Namespace(node="node", skill_dir=None, check_network=False)
        cfg = bridge.GmailConfig(email="user@example.com", app_password="a" * 16)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bridge.cmd_doctor(args, cfg)
        self.assertIn("DOCTOR PASS", buf.getvalue())

    def test_cnki_downloader_marks_records_as_simulated(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from cnki_pdf_downloader import CNKIPDFDownloader
        import json as _json

        with tempfile.TemporaryDirectory() as tmpdir:
            records = CNKIPDFDownloader(output_dir=tmpdir).search_and_download(topic="鲜味肽", count=1)
            self.assertTrue(records[0].simulated)
            self.assertEqual(records[0].provenance, "simulated-catalog")
            manifest = _json.loads((Path(tmpdir) / "cnki_download_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["simulated"])


class TestEventHooks(unittest.TestCase):
    """事件钩子（--on-event / --webhook）的离线测试。"""

    def test_on_event_command_receives_json(self):
        import gmail_cnki_bridge as bridge

        with tempfile.TemporaryDirectory() as tmpdir:
            sink = Path(tmpdir) / "event.json"
            event = {"event": "paper_ingested", "item_key": "CNKI_001", "title": "鲜味肽"}
            # 子进程把 stdin 原样落盘，验证事件 JSON 完整传递
            bridge.fire_hook(f'{sys.executable} -c "import sys,pathlib;'
                             f'pathlib.Path(sys.argv[1]).write_text(sys.stdin.read(),encoding=\'utf-8\')" '
                             f'"{sink}"', None, event)
            self.assertTrue(sink.exists())
            self.assertEqual(json.loads(sink.read_text(encoding="utf-8"))["item_key"], "CNKI_001")

    def test_on_event_sets_env_vars(self):
        import gmail_cnki_bridge as bridge

        with tempfile.TemporaryDirectory() as tmpdir:
            sink = Path(tmpdir) / "env.txt"
            event = {"event": "review_ready", "item_key": "CNKI_777", "pdf_path": "/tmp/a.pdf"}
            bridge.fire_hook(f'{sys.executable} -c "import os,sys,pathlib;'
                             f'pathlib.Path(sys.argv[1]).write_text('
                             f'os.environ[\'CNKI_EVENT_TYPE\']+\'|\'+os.environ[\'CNKI_ITEM_KEY\'],encoding=\'utf-8\')" '
                             f'"{sink}"', None, event)
            self.assertEqual(sink.read_text(encoding="utf-8"), "review_ready|CNKI_777")

    def test_hook_failure_is_swallowed(self):
        """钩子失败绝不能中断监听循环。"""
        import gmail_cnki_bridge as bridge

        bridge.fire_hook("this-command-definitely-does-not-exist-xyz", None, {"event": "x"})
        bridge.fire_hook(None, "http://127.0.0.1:1/nope", {"event": "x"})

    def test_watch_parser_accepts_push_mode(self):
        import gmail_cnki_bridge as bridge
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                sys.argv = ["gmail_cnki_bridge.py", "watch", "--help"]
                bridge.main()
        out = buf.getvalue()
        self.assertIn("--push-mode", out)
        self.assertIn("--on-event", out)
        self.assertIn("--webhook", out)


class TestHarvest(unittest.TestCase):
    """harvest：扫描邮箱既有 PDF 附件（离线，用假 IMAP 连接）。"""

    def _msg(self, subject, filename, payload, sender="prof@univ.edu"):
        from email.message import EmailMessage
        m = EmailMessage()
        m["Subject"] = subject
        m["From"] = sender
        m["To"] = "me@gmail.com"
        m["Message-ID"] = f"<{filename}@test>"
        m["Date"] = "Mon, 01 Jun 2026 10:00:00 +0800"
        m.set_content("正文")
        m.add_attachment(payload, maintype="application", subtype="pdf", filename=filename)
        return m

    def _patch_imap(self, bridge, messages):
        """用假的 IMAP4_SSL 顶替真实连接。"""
        class FakeIMAP:
            def __init__(self, *a, **k):
                self.store_calls = []
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def login(self, *a): return ("OK", [b""])
            def select(self, folder, readonly=False): return ("OK", [b"1"])
            def search(self, charset, *criteria):
                return ("OK", [b" ".join(str(i + 1).encode() for i in range(len(messages)))])
            def fetch(self, uid, spec):
                idx = int(uid) - 1
                # utf8 policy：附件名含中文时避免 ASCII 折行编码报错
                return ("OK", [(b"1", messages[idx].as_bytes(
                    policy=messages[idx].policy.clone(utf8=True)))])
            def store(self, uid, flag, val):
                self.store_calls.append((uid, val)); return ("OK", [b""])
        bridge.imaplib.IMAP4_SSL = FakeIMAP

    def _args(self, bridge, **over):
        import argparse as _a
        base = dict(folder="INBOX", since=None, before=None, sender=None, subject=None,
                    unseen_only=False, cnki_only=False, limit=0, newest_first=False,
                    mark_seen=False, dry_run=False)
        base.update(over)
        return _a.Namespace(**base)

    def test_harvest_ingests_arbitrary_pdf_attachments(self):
        import gmail_cnki_bridge as bridge
        orig = bridge.imaplib.IMAP4_SSL
        try:
            msgs = [
                self._msg("Fwd: 参考文献", "鲜味肽的分离鉴定研究.pdf", b"%PDF-1.4 aaa"),
                self._msg("paper", "Umami Peptide Screening.pdf", b"%PDF-1.4 bbb"),
            ]
            self._patch_imap(bridge, msgs)
            with tempfile.TemporaryDirectory() as tmp:
                lib = Path(tmp) / "lib"
                acc = bridge.LiteratureAccumulator(state_file=str(lib / "s.json"), batch_size=10)
                lib.mkdir(parents=True, exist_ok=True)
                reports = bridge.harvest_once(
                    bridge.GmailConfig("a@b.c", "p" * 16), acc, lib, "测试主题",
                    self._args(bridge))
                ingested = [r for r in reports if r["status"] == "ingested"]
                self.assertEqual(len(ingested), 2)
                for r in ingested:
                    self.assertTrue(Path(r["pdf_path"]).exists())
                # 标题应从附件名推断，而不是空
                self.assertTrue(any("鲜味肽" in r["title"] for r in ingested))
        finally:
            bridge.imaplib.IMAP4_SSL = orig

    def test_harvest_cnki_only_filters_english(self):
        import gmail_cnki_bridge as bridge
        orig = bridge.imaplib.IMAP4_SSL
        try:
            msgs = [
                self._msg("a", "鲜味肽受体机制.pdf", b"%PDF-1.4 ccc"),
                self._msg("b", "Nature Umami Review.pdf", b"%PDF-1.4 ddd"),
            ]
            self._patch_imap(bridge, msgs)
            with tempfile.TemporaryDirectory() as tmp:
                lib = Path(tmp) / "lib"
                acc = bridge.LiteratureAccumulator(state_file=str(lib / "s.json"), batch_size=10)
                lib.mkdir(parents=True, exist_ok=True)
                reports = bridge.harvest_once(
                    bridge.GmailConfig("a@b.c", "p" * 16), acc, lib, "测试",
                    self._args(bridge, cnki_only=True))
                self.assertEqual(len([r for r in reports if r["status"] == "ingested"]), 1)
                self.assertEqual(len([r for r in reports if r.get("reason") == "not_cnki"]), 1)
        finally:
            bridge.imaplib.IMAP4_SSL = orig

    def test_harvest_dry_run_writes_nothing(self):
        import gmail_cnki_bridge as bridge
        orig = bridge.imaplib.IMAP4_SSL
        try:
            self._patch_imap(bridge, [self._msg("a", "鲜味肽.pdf", b"%PDF-1.4 eee")])
            with tempfile.TemporaryDirectory() as tmp:
                lib = Path(tmp) / "lib"
                acc = bridge.LiteratureAccumulator(state_file=str(lib / "s.json"), batch_size=10)
                lib.mkdir(parents=True, exist_ok=True)
                reports = bridge.harvest_once(
                    bridge.GmailConfig("a@b.c", "p" * 16), acc, lib, "测试",
                    self._args(bridge, dry_run=True))
                self.assertEqual(reports[0]["status"], "would_ingest")
                self.assertFalse((lib / "index.json").exists())
        finally:
            bridge.imaplib.IMAP4_SSL = orig

    def test_harvest_dedups_identical_pdf(self):
        import gmail_cnki_bridge as bridge
        orig = bridge.imaplib.IMAP4_SSL
        try:
            same = b"%PDF-1.4 same-bytes"
            self._patch_imap(bridge, [self._msg("a", "鲜味肽甲.pdf", same),
                                      self._msg("b", "鲜味肽乙.pdf", same)])
            with tempfile.TemporaryDirectory() as tmp:
                lib = Path(tmp) / "lib"
                acc = bridge.LiteratureAccumulator(state_file=str(lib / "s.json"), batch_size=10)
                lib.mkdir(parents=True, exist_ok=True)
                reports = bridge.harvest_once(
                    bridge.GmailConfig("a@b.c", "p" * 16), acc, lib, "测试",
                    self._args(bridge))
                self.assertEqual(len([r for r in reports if r["status"] == "ingested"]), 1)
                self.assertEqual(len([r for r in reports if r["status"] == "duplicate"]), 1)
        finally:
            bridge.imaplib.IMAP4_SSL = orig

    def test_harvest_limit(self):
        import gmail_cnki_bridge as bridge
        orig = bridge.imaplib.IMAP4_SSL
        try:
            self._patch_imap(bridge, [self._msg(f"s{i}", f"鲜味肽{i}.pdf", f"%PDF-{i}".encode())
                                      for i in range(5)])
            with tempfile.TemporaryDirectory() as tmp:
                lib = Path(tmp) / "lib"
                acc = bridge.LiteratureAccumulator(state_file=str(lib / "s.json"), batch_size=10)
                lib.mkdir(parents=True, exist_ok=True)
                reports = bridge.harvest_once(
                    bridge.GmailConfig("a@b.c", "p" * 16), acc, lib, "测试",
                    self._args(bridge, limit=2))
                self.assertEqual(len(reports), 2)
        finally:
            bridge.imaplib.IMAP4_SSL = orig

    def test_to_imap_date(self):
        import gmail_cnki_bridge as bridge
        self.assertEqual(bridge.to_imap_date("2026-06-01"), "01-Jun-2026")
        self.assertEqual(bridge.to_imap_date("01-Jun-2026"), "01-Jun-2026")

    def test_build_search_criteria(self):
        import gmail_cnki_bridge as bridge
        import argparse as _a
        args = _a.Namespace(unseen_only=True, since="2026-01-05", before=None,
                            sender="prof@univ.edu", subject=None)
        self.assertEqual(bridge.build_search_criteria(args),
                         ["UNSEEN", "SINCE", "05-Jan-2026", "FROM", "prof@univ.edu"])
        empty = _a.Namespace(unseen_only=False, since=None, before=None, sender=None, subject=None)
        self.assertEqual(bridge.build_search_criteria(empty), ["ALL"])

    def test_ingest_report_exposes_pdf_path_and_title(self):
        """回归：事件钩子读的是 pdf_path/title，process_ingest_message 必须提供。"""
        import gmail_cnki_bridge as bridge
        entry = {"item_key": "CNKI_900", "title": "鲜味肽测试", "authors": [], "year": "2026",
                 "journal": "", "doi": "", "abstract": "", "cnki_url": "", "source": "cnki"}
        msg = bridge.build_ingest_message(entry, b"%PDF-1.4 zzz",
                                          bridge.GmailConfig("a@b.c", "p"), to="a@b.c")
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib"
            acc = bridge.LiteratureAccumulator(state_file=str(lib / "s.json"), batch_size=10)
            rep = bridge.process_ingest_message(msg, lib, acc, "测试")
            self.assertEqual(rep["status"], "ingested")
            self.assertEqual(rep["title"], "鲜味肽测试")
            self.assertTrue(Path(rep["pdf_path"]).exists())


class TestMessageParsing(unittest.TestCase):
    """回归：原始邮件必须解析成 EmailMessage（modern policy），否则下游 iter_parts 会崩。"""

    def test_parse_message_bytes_returns_modern_message(self):
        import gmail_cnki_bridge as bridge
        from email.message import EmailMessage

        m = EmailMessage()
        m["Subject"] = "测试主题"
        m["Message-ID"] = "<x@y>"
        m.set_content("正文")
        m.add_attachment(b"%PDF-1.4 q", maintype="application", subtype="pdf", filename="a.pdf")

        parsed = bridge.parse_message_bytes(m.as_bytes(policy=m.policy.clone(utf8=True)))
        self.assertTrue(hasattr(parsed, "iter_parts"))
        self.assertTrue(hasattr(parsed, "get_body"))
        self.assertEqual(len(bridge.extract_pdf_parts(parsed)), 1)
        # Message-ID 必须是可 .strip() 的字符串语义
        self.assertIn("x@y", str(parsed.get("Message-ID")))

    def test_parse_message_bytes_tolerates_garbage(self):
        import gmail_cnki_bridge as bridge
        parsed = bridge.parse_message_bytes(b"not-a-real-email\x00\xff")
        self.assertIsNotNone(parsed)


class TestDeliverToCloudSpark(unittest.TestCase):
    """deliver：把成果寄给只能收 Gmail 的云端 Spark。"""

    def _batch(self, root: Path, big=False):
        b = root / "reviews" / "batch_1"
        b.mkdir(parents=True, exist_ok=True)
        (b / "thesis_chapter1_review.md").write_text("# 综述\n\nIC50 = 3.2 μM（第5页）。", encoding="utf-8")
        payload = b"x" * (20 * 1024 * 1024) if big else b'{"a":1}'
        (b / "arta_synthesis_payload.json").write_bytes(payload)
        (b / "arta_ppt_payload.json").write_text('{"b":2}', encoding="utf-8")
        return b

    def test_review_email_inlines_fulltext_and_instructions(self):
        import gmail_cnki_bridge as bridge
        with tempfile.TemporaryDirectory() as tmp:
            b = self._batch(Path(tmp))
            msg, notes = bridge.build_review_delivery_message(
                bridge.GmailConfig("me@gmail.com", "p" * 16), "鲜味肽", b, "spark@cloud.ai",
                cloud_links=["https://drive.google.com/x"])
            body = msg.get_body("plain").get_content()
            self.assertIn("【你的任务】", body)          # 任务说明
            self.assertIn("IC50 = 3.2", body)            # 综述全文内联
            self.assertIn("https://drive.google.com/x", body)  # 云盘链接
            self.assertIn("严禁编造", body)               # 防幻觉红线
            self.assertTrue(msg["Subject"].startswith(bridge.DELIVER_PREFIX))
            self.assertEqual(msg["To"], "spark@cloud.ai")
            names = [p.get_filename() for p in msg.iter_attachments()]
            self.assertIn("thesis_chapter1_review.md", names)
            self.assertEqual(notes, [])

    def test_oversized_attachment_is_skipped_with_note(self):
        import gmail_cnki_bridge as bridge
        with tempfile.TemporaryDirectory() as tmp:
            b = self._batch(Path(tmp), big=True)
            msg, notes = bridge.build_review_delivery_message(
                bridge.GmailConfig("me@gmail.com", "p" * 16), "鲜味肽", b, "spark@cloud.ai")
            self.assertTrue(any("超限" in n for n in notes))
            names = [p.get_filename() for p in msg.iter_attachments()]
            self.assertNotIn("arta_synthesis_payload.json", names)
            self.assertIn("thesis_chapter1_review.md", names)

    def test_card_email_carries_pdf_and_extraction_task(self):
        import gmail_cnki_bridge as bridge
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "p.pdf"
            pdf.write_bytes(b"%PDF-1.4 body")
            entry = bridge.normalize_entries([{"item_key": "CNKI_1", "title": "鲜味肽研究"}])[0]
            msg, notes = bridge.build_card_delivery_message(
                bridge.GmailConfig("me@gmail.com", "p"), "鲜味肽", entry, pdf, "spark@cloud.ai")
            body = msg.get_body("plain").get_content()
            self.assertIn("PaperCard", body)
            self.assertIn("标注所在页码", body)
            self.assertTrue(msg["Subject"].startswith(bridge.CARD_PREFIX))
            self.assertEqual([p.get_filename() for p in msg.iter_attachments()], ["CNKI_1.pdf"])
            self.assertEqual(notes, [])

    def test_find_latest_batch_sorts_numerically(self):
        import gmail_cnki_bridge as bridge
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in (1, 2, 10):
                (root / "reviews" / f"batch_{n}").mkdir(parents=True)
            self.assertEqual(bridge.find_latest_batch(root).name, "batch_10")

    def test_find_latest_batch_none_when_empty(self):
        import gmail_cnki_bridge as bridge
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(bridge.find_latest_batch(Path(tmp)))

    def test_auto_deliver_noop_without_target(self):
        """没配 --deliver-to / SPARK_EMAIL 时必须安静跳过，不能抛错。"""
        import gmail_cnki_bridge as bridge
        import argparse as _a
        import os as _os
        saved = _os.environ.pop("SPARK_EMAIL", None)
        try:
            args = _a.Namespace(deliver_to=None, topic="t", cloud_link=[])
            bridge.auto_deliver_if_configured(args, bridge.GmailConfig("a@b.c", "p"),
                                              {"chapter1_path": "/nonexistent/batch_1/x.md"})
        finally:
            if saved is not None:
                _os.environ["SPARK_EMAIL"] = saved

    def test_auto_deliver_swallows_send_failure(self):
        """寄送失败不能中断监听循环，成果仍在本地。"""
        import gmail_cnki_bridge as bridge
        import argparse as _a
        orig = bridge.send_message
        try:
            def boom(cfg, msg):
                raise RuntimeError("smtp down")
            bridge.send_message = boom
            with tempfile.TemporaryDirectory() as tmp:
                b = self._batch(Path(tmp))
                args = _a.Namespace(deliver_to="spark@cloud.ai", topic="t", cloud_link=[])
                bridge.auto_deliver_if_configured(
                    args, bridge.GmailConfig("a@b.c", "p" * 16),
                    {"chapter1_path": str(b / "thesis_chapter1_review.md")})
        finally:
            bridge.send_message = orig
