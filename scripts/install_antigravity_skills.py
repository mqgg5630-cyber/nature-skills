#!/usr/bin/env python3
"""
Antigravity (反重力) Skills One-Click Installer & Deployer
==========================================================
Deploys the full suite of Nature-Skills (Review + Experimental Research Writing)
directly into Antigravity IDE / Agent workspace.

Supported Antigravity Target Directories:
- Windows default: C:\\Users\\<Username>\\.gemini\\antigravity-ide\\skills\\
- Workspace default: ./.agents/skills/ or ~/.antigravity/skills/

Skills Included:
1. [Experimental Research Writing (实验研究类 SCI 论文写作)]
   - nature-writing (Research, Methods, Results, Discussion)
   - nature-statistics (Sample size, p-value, ANOVA, error bars)
   - nature-figure (Publication-grade Python/R scientific plotting)
   - nature-experiment-log (Lab notebook & raw data logging)
   - nature-proposal-writer (Grant & thesis proposal state-machine)
   - nature-reviewer (Simulated 3-reviewer pre-submission audit)
2. [Literature Review Writing (科研综述类写作)]
   - nature-writing (paper_type: review)
   - nature-literature-pipeline (Review compilation workflow)
   - nature-paper-card (01-16 Paper Cards fact extraction)
   - nature-reader (Bilingual full-text parser)
3. [Support & Polishing (润色与核验)]
   - nature-polishing (Nature-level English polishing)
   - nature-ref-verifier (Citation verification)
   - nature-shared (Core templates & journal guidelines)

Usage:
  # Deploy to default Antigravity path or local workspace
  python3 scripts/install_antigravity_skills.py

  # Deploy to custom directory
  python3 scripts/install_antigravity_skills.py --target-dir "C:/Users/文少/.gemini/antigravity-ide/skills"
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

ALL_SKILLS = [
    "nature-writing",
    "nature-statistics",
    "nature-figure",
    "nature-experiment-log",
    "nature-proposal-writer",
    "nature-reviewer",
    "nature-paper-card",
    "nature-reader",
    "nature-literature-pipeline",
    "nature-polishing",
    "nature-ref-verifier",
    "nature-citation",
    "nature-data",
    "nature-downloader",
    "nature-shared",
]


def detect_antigravity_dir(custom_path: str | None = None) -> Path:
    if custom_path:
        return Path(custom_path).expanduser()

    # Check common Antigravity paths
    home = Path.home()
    candidates = [
        home / ".gemini" / "antigravity-ide" / "skills",
        home / ".antigravity" / "skills",
        REPO_ROOT / ".agents" / "skills",
        REPO_ROOT / "antigravity_skills",
    ]

    for c in candidates:
        if c.parent.exists():
            return c

    return candidates[0]


def install_skills(target_dir: Path, skills: List[str] | None = None) -> List[Tuple[str, str]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    selected_skills = skills or ALL_SKILLS
    installed = []

    print("=" * 75)
    print("🚀 [Antigravity Skills 一键部署] 开始安装全套科研与实验写作 Skills")
    print("=" * 75)
    print(f"📂 目标反重力安装路径: {target_dir.resolve()}\n")

    for skill_name in selected_skills:
        src = SKILLS_DIR / skill_name
        if not src.exists():
            print(f"⚠️ 跳过未找到的技能: {skill_name}")
            continue

        dst = target_dir / skill_name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        installed.append((skill_name, str(dst.resolve())))
        print(f"  ✅ [已安装] {skill_name:<28} -> {dst.name}")

    print("\n" + "=" * 75)
    print(f"🎉 全部 {len(installed)} 个 Nature-Skills 已成功部署至反重力 (Antigravity)！")
    print("=" * 75)
    return installed


def main():
    parser = argparse.ArgumentParser(description="Install Nature-Skills to Antigravity")
    parser.add_argument("--target-dir", type=str, default=None, help="Antigravity skills directory")
    args = parser.parse_args()

    target = detect_antigravity_dir(args.target_dir)
    install_skills(target)


if __name__ == "__main__":
    main()
