#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

sample = '''default_profile = "demo"

[profiles.live]
site = "global"
api_key = "REPLACE_ME"
secret_key = "REPLACE_ME"
passphrase = "REPLACE_ME"

[profiles.demo]
site = "global"
api_key = "REPLACE_ME"
secret_key = "REPLACE_ME"
passphrase = "REPLACE_ME"
demo = true
'''

path = Path.home() / ".okx" / "config.toml"
path.parent.mkdir(parents=True, exist_ok=True)
if path.exists():
    raise SystemExit(f"Refusing to overwrite existing {path}")
path.write_text(sample, encoding="utf-8")
print(path)
