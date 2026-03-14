#!/usr/bin/env python3
from __future__ import annotations

import urllib.request
from pathlib import Path

RAW_BASE = "https://raw.githubusercontent.com/okx/agent-skills/master/skills"
SKILLS = ["okx-cex-market", "okx-cex-trade", "okx-cex-portfolio", "okx-cex-bot"]
WORKSPACE_SKILLS = Path(__file__).resolve().parents[1] / "skills"

for name in SKILLS:
    url = f"{RAW_BASE}/{name}/SKILL.md"
    target = WORKSPACE_SKILLS / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {url} -> {target}")
    with urllib.request.urlopen(url, timeout=30) as r:
        target.write_bytes(r.read())

print("Done.")
