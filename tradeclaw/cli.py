from __future__ import annotations

import argparse
import json
from pathlib import Path

from .app import TradeClawApp


def main() -> int:
    parser = argparse.ArgumentParser(description="TG public-channel → OpenClaw brain → OKX executor")
    parser.add_argument("--config", default="config.json", help="Path to app config JSON")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle and exit")
    parser.add_argument("--dump-config", action="store_true", help="Print resolved config and exit")
    args = parser.parse_args()

    app = TradeClawApp(args.config)
    if args.dump_config:
        print(json.dumps(app.config.__dict__, default=lambda o: o.__dict__, ensure_ascii=False, indent=2))
        return 0
    if args.once:
        print(json.dumps(app.run_once(), ensure_ascii=False, indent=2))
        return 0
    app.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
