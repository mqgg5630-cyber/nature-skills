#!/usr/bin/env python3
"""Unit tests for CNKI to Gmail and Spark Review Bridge."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from cnki_gmail_agent_bridge import CNKIGmailAgentBridge, run_full_bridge


class TestCNKIGmailAgentBridge(unittest.TestCase):
    def test_full_bridge_loop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = CNKIGmailAgentBridge(output_dir=tmpdir)
            emails = bridge.simulate_step1_push_to_gmail(topic="鲜味肽", count=2)
            self.assertEqual(len(emails), 2)

            records = bridge.execute_step2_agent_download_pdf(emails)
            self.assertEqual(len(records), 2)
            for r in records:
                self.assertTrue(Path(r.local_pdf_path).exists())

            review_res = bridge.execute_step3_generate_review(topic="鲜味肽", records=records)
            self.assertTrue(Path(review_res["chapter1_path"]).exists())
            self.assertTrue(Path(review_res["payload_path"]).exists())
            self.assertTrue(Path(review_res["ppt_path"]).exists())


if __name__ == "__main__":
    unittest.main()
