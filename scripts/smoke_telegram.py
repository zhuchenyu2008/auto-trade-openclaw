#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from smoke_utils import mirror_source_local_env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.demo.local.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    src_path = str(root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from tg_okx_auto_trade.runtime import Runtime

    source_path = Path(args.config)
    if not source_path.is_absolute():
        source_path = (root / source_path).resolve()
    source_config = json.loads(source_path.read_text(encoding="utf-8"))
    os.environ["TG_OKX_DISABLE_TOPIC_SEND"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = json.loads(json.dumps(source_config))
        config["runtime"]["data_dir"] = str(tmp_path / "runtime")
        config["runtime"]["sqlite_path"] = str(tmp_path / "runtime" / "app.db")
        config["ai"]["provider"] = "heuristic"
        config["okx"]["enabled"] = False
        config["telegram"]["bot_token"] = "demo-bot-token"
        config["telegram"]["channels"] = [
            {
                "id": "vip-btc",
                "name": "VIP BTC",
                "source_type": "bot_api",
                "chat_id": "-1001",
                "channel_username": "",
                "enabled": True,
                "priority": 100,
                "parse_profile_id": "default",
                "strategy_profile_id": "default",
                "risk_profile_id": "default",
                "paper_trading_enabled": True,
                "live_trading_enabled": False,
                "listen_new_messages": True,
                "listen_edits": True,
                "listen_deletes": False,
                "reconcile_interval_seconds": 30,
                "dedup_window_seconds": 3600,
                "notes": "",
            }
        ]
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        runtime = Runtime(config_path)
        try:
            processed_new = runtime.telegram._process_update(
                {
                    "update_id": 1,
                    "channel_post": {
                        "message_id": 101,
                        "date": 100,
                        "text": "LONG BTCUSDT SIZE 1",
                        "chat": {"id": -1001, "username": "vipbtc"},
                    },
                },
                runtime.process_message,
                runtime.config_manager.get(),
            )
            processed_edit = runtime.telegram._process_update(
                {
                    "update_id": 2,
                    "edited_channel_post": {
                        "message_id": 101,
                        "date": 100,
                        "edit_date": 120,
                        "text": "REVERSE SHORT BTCUSDT SIZE 2",
                        "chat": {"id": -1001, "username": "vipbtc"},
                    },
                },
                runtime.process_message,
                runtime.config_manager.get(),
            )
            if not processed_new or not processed_edit:
                raise RuntimeError("expected bot_api watcher updates to be accepted")

            with mock.patch.object(
                runtime.telegram,
                "_get_chat_history",
                return_value=[
                    {
                        "message_id": 202,
                        "date": 200,
                        "text": "LONG ETHUSDT SIZE 3",
                        "chat": {"id": -1001, "username": "vipbtc"},
                    },
                    {
                        "message_id": 203,
                        "date": 210,
                        "edit_date": 230,
                        "text": "SHORT SOLUSDT SIZE 4",
                        "chat": {"id": -1001, "username": "vipbtc"},
                    },
                ],
            ):
                reconcile = runtime.reconcile_now()

            snapshot = runtime.snapshot()
            if reconcile["replayed_messages"] != 2:
                raise RuntimeError(f"expected reconcile to replay 2 buffered messages, got {reconcile['replayed_messages']}")
            if snapshot["messages"][0]["status"] != "EXECUTED":
                raise RuntimeError(f"expected latest reconciled message to execute, got {snapshot['messages'][0]['status']}")
            if snapshot["health"]["telegram_watcher"]["status"] not in {"idle", "configured", "connected"}:
                raise RuntimeError("unexpected telegram watcher health state")

            print(
                json.dumps(
                    {
                        "watcher_new_update": processed_new,
                        "watcher_edit_update": processed_edit,
                        "reconcile_status": reconcile["status"],
                        "replayed_messages": reconcile["replayed_messages"],
                        "orders_recorded": len(snapshot["orders"]),
                        "latest_message_status": snapshot["messages"][0]["status"],
                    },
                    indent=2,
                )
            )
            return 0
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
