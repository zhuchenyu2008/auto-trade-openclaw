#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src_path = root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    from tg_okx_auto_trade.fixture_suite import run_fixture_suite

    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, help="Fixture directory to execute.")
    args = parser.parse_args()

    fixtures_dir = Path(args.fixtures)
    if not fixtures_dir.is_absolute():
        fixtures_dir = (root / fixtures_dir).resolve()

    result = run_fixture_suite(fixtures_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["suite_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
