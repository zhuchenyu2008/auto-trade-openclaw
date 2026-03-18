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

    from tg_okx_auto_trade.models import NormalizedMessage
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
        config["telegram"]["bot_token"] = "demo-bot-token"
        config["okx"]["enabled"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        runtime = Runtime(config_path)
        try:
            help_result = runtime.run_operator_command("/help", source="smoke")
            if not help_result.get("handled") or "/topic-test" not in help_result.get("reply", ""):
                raise RuntimeError("expected /help operator command to be handled")
            status_result = runtime.run_operator_command("/status", source="smoke")
            if not status_result.get("handled") or status_result.get("command") != "status":
                raise RuntimeError("expected /status operator command to be handled")
            readiness_result = runtime.run_operator_command("/readiness", source="smoke")
            if not readiness_result.get("handled") or "[readiness]" not in readiness_result.get("reply", ""):
                raise RuntimeError("expected /readiness operator command to be handled")
            paths_result = runtime.run_operator_command("/paths", source="smoke")
            if not paths_result.get("handled") or "[paths]" not in paths_result.get("reply", ""):
                raise RuntimeError("expected /paths operator command to be handled")
            channels_result = runtime.run_operator_command("/channels", source="smoke")
            if not channels_result.get("handled") or "[channels]" not in channels_result.get("reply", ""):
                raise RuntimeError("expected /channels operator command to be handled")
            runtime.inject_message("LONG BTCUSDT SIZE 1", "-1000000000000", 1201)
            signals_result = runtime.run_operator_command("/signals 3", source="smoke")
            if not signals_result.get("handled") or "[signals]" not in signals_result.get("reply", ""):
                raise RuntimeError("expected /signals operator command to be handled")
            risk_result = runtime.run_operator_command("/risk", source="smoke")
            if not risk_result.get("handled") or "[risk]" not in risk_result.get("reply", ""):
                raise RuntimeError("expected /risk operator command to be handled")
            positions_result = runtime.run_operator_command("/positions", source="smoke")
            if not positions_result.get("handled") or "[positions]" not in positions_result.get("reply", ""):
                raise RuntimeError("expected /positions operator command to be handled")
            orders_result = runtime.run_operator_command("/orders 3", source="smoke")
            if not orders_result.get("handled") or "[orders]" not in orders_result.get("reply", ""):
                raise RuntimeError("expected /orders operator command to be handled")
            pause_result = runtime.run_operator_command("/pause smoke hold", source="smoke")
            if not pause_result.get("handled") or runtime.snapshot()["operator_state"]["paused"] is not True:
                raise RuntimeError("expected /pause operator command to pause runtime")
            resume_result = runtime.run_operator_command("/resume smoke resume", source="smoke")
            if not resume_result.get("handled") or runtime.snapshot()["operator_state"]["paused"] is not False:
                raise RuntimeError("expected /resume operator command to resume runtime")
            reconcile_result = runtime.run_operator_command("/reconcile", source="smoke")
            if not reconcile_result.get("handled") or reconcile_result.get("status") not in {"ok", "skipped"}:
                raise RuntimeError("expected /reconcile operator command to be handled")
            topic_test_result = runtime.run_operator_command("/topic-test", source="smoke")
            if not topic_test_result.get("handled") or topic_test_result.get("status") != "disabled":
                raise RuntimeError("expected /topic-test operator command to respect disabled topic delivery")
            close_result = runtime.run_operator_command("/close BTC-USDT-SWAP", source="smoke")
            if not close_result.get("handled") or not close_result.get("closed"):
                raise RuntimeError("expected /close operator command to flatten the open position")

            operator_message = NormalizedMessage.from_telegram(
                "bot_api",
                "new",
                {
                    "message_id": 1001,
                    "date": 100,
                    "text": "/status",
                    "message_thread_id": 2080,
                    "chat": {"id": -1003720752566, "username": "smallclaw"},
                },
            )
            with mock.patch.object(runtime, "_send_topic_update") as send_topic:
                runtime.process_operator_message(operator_message)
            if not send_topic.called:
                raise RuntimeError("expected process_operator_message to emit a topic reply")

            operator_callback = mock.Mock()
            message_callback = mock.Mock()
            runtime.telegram.operator_callback = operator_callback
            handled = runtime.telegram._process_update(
                {
                    "update_id": 77,
                    "message": {
                        "message_id": 1002,
                        "date": 110,
                        "text": "/pause smoke hold",
                        "message_thread_id": 2080,
                        "chat": {"id": -1003720752566, "username": "smallclaw"},
                    },
                },
                message_callback,
                runtime.config_manager.get(),
            )
            if not handled or not operator_callback.called or message_callback.called:
                raise RuntimeError("expected watcher to route operator-topic update to operator callback only")

            print(
                json.dumps(
                    {
                        "operator_help_command": help_result["status"],
                        "operator_status_command": status_result["status"],
                        "operator_readiness_command": readiness_result["status"],
                        "operator_paths_command": paths_result["status"],
                        "operator_channels_command": channels_result["status"],
                        "operator_signals_command": signals_result["status"],
                        "operator_risk_command": risk_result["status"],
                        "operator_positions_command": positions_result["status"],
                        "operator_orders_command": orders_result["status"],
                        "operator_pause_command": pause_result["status"],
                        "operator_resume_command": resume_result["status"],
                        "operator_reconcile_command": reconcile_result["status"],
                        "operator_topic_test_command": topic_test_result["status"],
                        "operator_close_command": close_result["status"],
                        "process_operator_message_reply": True,
                        "watcher_operator_route": True,
                    },
                    indent=2,
                )
            )
            return 0
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
