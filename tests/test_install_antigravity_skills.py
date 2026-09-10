#!/usr/bin/env python3
"""Unit tests for Antigravity Skills Installer."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from install_antigravity_skills import install_skills, ALL_SKILLS


class TestInstallAntigravitySkills(unittest.TestCase):
    def test_install_skills(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            installed = install_skills(Path(tmpdir))
            self.assertEqual(len(installed), len(ALL_SKILLS))
            for name, path_str in installed:
                self.assertTrue(Path(path_str).exists())
                self.assertTrue((Path(path_str) / "SKILL.md").exists() or (Path(path_str) / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
