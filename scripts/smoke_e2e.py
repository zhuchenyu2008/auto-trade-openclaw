#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from smoke_utils import mirror_source_local_env

PIN_HASH = "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.demo.local.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    src_path = str(root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from tg_okx_auto_trade.runtime import Runtime
    from tg_okx_auto_trade.web import WebController

    source_path = Path(args.config)
    if not source_path.is_absolute():
        source_path = (root / source_path).resolve()
    source_config = json.loads(source_path.read_text(encoding="utf-8"))
    os.environ["TG_OKX_DISABLE_TOPIC_SEND"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = json.loads(json.dumps(source_config))
        config["web"]["pin_hash"] = PIN_HASH
        config["runtime"]["data_dir"] = str(tmp_path / "runtime")
        config["runtime"]["sqlite_path"] = str(tmp_path / "runtime" / "app.db")
        config["runtime"]["config_reload_seconds"] = 1
        config["ai"]["provider"] = "heuristic"
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
                "notes": "e2e smoke channel",
            }
        ]
        config["okx"]["enabled"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        runtime = Runtime(config_path)
        runtime.start(background=False)
        try:
            controller = WebController(runtime)
            status, headers, _ = controller.route(
                "POST",
                "/login",
                body=b"pin=123456",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if status != 303:
                raise RuntimeError("expected Web login to succeed in e2e smoke")
            session_cookie = headers["Set-Cookie"]

            handled = runtime.telegram._process_update(
                {
                    "update_id": 201,
                    "channel_post": {
                        "message_id": 3201,
                        "date": 100,
                        "text": "LONG BTCUSDT SIZE 2",
                        "chat": {"id": -1001, "username": "vipbtc"},
                    },
                },
                runtime.process_message,
                runtime.config_manager.get(),
            )
            if not handled:
                raise RuntimeError("expected Bot API watcher update to be accepted in e2e smoke")

            status, _, state = controller.route("GET", "/api/state", headers={"Cookie": session_cookie})
            if status != 200:
                raise RuntimeError("expected authenticated /api/state in e2e smoke")
            if state["orders"][0]["status"] != "filled":
                raise RuntimeError("expected Telegram-driven order to be filled in e2e smoke")
            if state["positions"][0]["payload"]["side"] != "long":
                raise RuntimeError("expected a long simulated position after Telegram-driven signal")
            if state["capabilities"]["telegram_ingestion"]["status"] != "ready":
                raise RuntimeError("expected telegram_ingestion capability to be ready in e2e smoke")
            if state["capabilities"]["current_operating_profile"]["status"] != "ready":
                raise RuntimeError("expected current operating profile to be ready in e2e smoke")
            if state["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
                raise RuntimeError("expected operator topic link in e2e smoke state")

            status, _, operator = controller.route(
                "POST",
                "/api/actions/operator-command",
                body=json.dumps({"text": "/status"}).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or operator.get("command") != "status":
                raise RuntimeError("expected /status operator command to be handled in e2e smoke")

            status, _, closed = controller.route(
                "POST",
                "/api/positions/close",
                body=json.dumps({"symbol": "BTC-USDT-SWAP"}).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or closed["closed"][0]["status"] != "filled":
                raise RuntimeError("expected operator close path to flatten the simulated position")

            final_state = runtime.public_snapshot()
            if final_state["positions"][0]["payload"]["qty"] != 0.0:
                raise RuntimeError("expected the final simulated position to be flat in e2e smoke")

            print(
                json.dumps(
                    {
                        "telegram_update_handled": handled,
                        "current_profile": state["capabilities"]["current_operating_profile"]["status"],
                        "telegram_ingestion_status": state["capabilities"]["telegram_ingestion"]["status"],
                        "message_status": state["messages"][0]["status"],
                        "order_status": state["orders"][0]["status"],
                        "operator_command_status": operator["status"],
                        "close_status": closed["closed"][0]["status"],
                        "topic_target_link": state["run_paths"]["topic_target_link"],
                        "final_position_side": final_state["positions"][0]["payload"]["side"],
                    },
                    indent=2,
                )
            )
            return 0
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
