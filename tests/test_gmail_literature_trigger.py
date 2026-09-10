#!/usr/bin/env python3
"""Unit tests for the Gmail & Zotero 10-Paper Review Trigger."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from gmail_literature_trigger import (
    LiteratureAccumulator,
    QueuedPaper,
    simulate_gmail_stream,
)


class TestGmailLiteratureTrigger(unittest.TestCase):
    def test_accumulator_queue_and_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            accum = LiteratureAccumulator(state_file=str(state_file), batch_size=3)

            p1 = QueuedPaper("K1", "Title 1", ["A1"], "2024", "J1", "10.1/1", "Abs 1", "2026-01-01")
            p2 = QueuedPaper("K2", "Title 2", ["A2"], "2024", "J2", "10.1/2", "Abs 2", "2026-01-01")
            p3 = QueuedPaper("K3", "Title 3", ["A3"], "2024", "J3", "10.1/3", "Abs 3", "2026-01-01")

            # Add 2 papers (threshold not reached)
            r1 = accum.add_paper(p1, topic="Test", output_dir=tmpdir)
            self.assertIsNone(r1)
            self.assertEqual(len(accum.queue), 1)

            r2 = accum.add_paper(p2, topic="Test", output_dir=tmpdir)
            self.assertIsNone(r2)
            self.assertEqual(len(accum.queue), 2)

            # Add 3rd paper (threshold reached!)
            r3 = accum.add_paper(p3, topic="Test", output_dir=tmpdir)
            self.assertIsNotNone(r3)
            self.assertEqual(r3["batch_id"], 1)
            self.assertEqual(accum.processed_batches, 1)
            self.assertEqual(len(accum.queue), 0)

            # Check output files
            self.assertTrue(Path(r3["chapter1_path"]).exists())
            self.assertTrue(Path(r3["payload_path"]).exists())
            self.assertTrue(Path(r3["ppt_path"]).exists())

    def test_simulation_run(self):
        result = simulate_gmail_stream(batch_size=10, topic="Test Umami")
        self.assertIn("batch_id", result)
        self.assertEqual(result["paper_count"], 10)


if __name__ == "__main__":
    unittest.main()
