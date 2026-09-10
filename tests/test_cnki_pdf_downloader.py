#!/usr/bin/env python3
"""Unit tests for CNKI PDF Downloader."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from cnki_pdf_downloader import CNKIPDFDownloader, CNKIPaperRecord


class TestCNKIPDFDownloader(unittest.TestCase):
    def test_search_and_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            downloader = CNKIPDFDownloader(output_dir=tmpdir)
            records = downloader.search_and_download(topic="鲜味肽", count=2)
            self.assertEqual(len(records), 2)
            for r in records:
                self.assertTrue(Path(r.local_pdf_path).exists())
                self.assertGreater(r.file_size_bytes, 0)
                self.assertEqual(r.status, "Downloaded_Local_OK")

            manifest = Path(tmpdir) / "cnki_download_manifest.json"
            self.assertTrue(manifest.exists())


if __name__ == "__main__":
    unittest.main()
