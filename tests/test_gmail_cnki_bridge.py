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
