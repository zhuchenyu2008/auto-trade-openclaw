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

    from tg_okx_auto_trade.fixture_suite import write_seed_fixture_corpus

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="tests/fixtures/public_web",
        help="Output root for the public_web fixture corpus.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = (root / output_dir).resolve()

    manifest = write_seed_fixture_corpus(output_dir)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
