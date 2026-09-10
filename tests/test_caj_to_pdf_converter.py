#!/usr/bin/env python3
"""Unit tests for CAJ to PDF Converter."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from caj_to_pdf_converter import CAJ2PDFConverter


class TestCAJ2PDFConverter(unittest.TestCase):
    def test_caj_conversion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            sample_caj = tmp_path / "绍兴黄酒鲜味肽鉴定及其对滋味的影响_常瑞.caj"
            sample_caj.write_bytes(b"CAJ 1.0\n" + b"0" * 10000)

            out_pdf = tmp_path / "绍兴黄酒鲜味肽鉴定及其对滋味的影响_常瑞.pdf"
            ok, msg, pages = CAJ2PDFConverter.convert_caj_to_pdf(sample_caj, out_pdf)
            self.assertTrue(ok)
            self.assertTrue(out_pdf.exists())
            self.assertGreaterEqual(pages, 1)

    def test_batch_convert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "src"
            dst_dir = Path(tmpdir) / "dst"
            src_dir.mkdir()

            (src_dir / "paper1_张三.caj").write_bytes(b"CAJ 1.0\n" + b"x" * 5000)
            (src_dir / "paper2_李四.caj").write_bytes(b"CAJ 1.0\n" + b"y" * 8000)

            results = CAJ2PDFConverter.batch_convert_directory(src_dir, dst_dir)
            self.assertEqual(len(results), 2)
            self.assertTrue((dst_dir / "paper1_张三.pdf").exists())
            self.assertTrue((dst_dir / "paper2_李四.pdf").exists())


if __name__ == "__main__":
    unittest.main()
