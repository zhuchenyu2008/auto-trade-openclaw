#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD"
python3 -m unittest discover -s tests -v
okx market ticker BTC-USDT --json >/dev/null
okx-trade-mcp --help >/dev/null
python3 -m tradeclaw.cli --config config.example.json --once
