#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from smoke_utils import mirror_source_local_env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.demo.local.json")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--size", type=float, default=1.0)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    src_path = str(root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from tg_okx_auto_trade.runtime import Runtime

    source_path = (root / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    source_config = json.loads(source_path.read_text(encoding="utf-8"))
    if not source_config.get("okx", {}).get("enabled"):
        raise RuntimeError("OKX demo smoke requires okx.enabled=true")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = json.loads(json.dumps(source_config))
        config["runtime"]["data_dir"] = str(tmp_path / "runtime")
        config["runtime"]["sqlite_path"] = str(tmp_path / "runtime" / "app.db")
        config["ai"]["provider"] = "heuristic"
        config["telegram"]["bot_token"] = ""
        config["telegram"]["report_topic"] = ""
        config["telegram"]["operator_target"] = ""
        config["trading"]["mode"] = "demo"
        config["trading"]["execution_mode"] = "automatic"
        config["trading"]["paused"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        runtime = Runtime(config_path)
        runtime.start(background=False)
        try:
            report = runtime.public_verification_report()
            if report["status"] == "error":
                raise RuntimeError("verify report returned error status for demo smoke")
            signal_text = f"OPEN LONG {args.symbol} UNIQUE_SMOKE_9901"
            runtime.inject_message(
                text=signal_text,
                chat_id="-1000000000000",
                message_id=9901,
                event_type="new",
                use_configured_okx_path=True,
            )
            snapshot = runtime.snapshot()
            current_message = _find_message(snapshot, 9901, 1)
            if current_message and current_message["status"] == "EXECUTION_FAILED":
                detail = snapshot["health"]["okx_rest"]["detail"]
                if _is_network_blocked(detail):
                    print(
                        json.dumps(
                            {
                                "status": "skipped",
                                "reason": detail,
                                "okx_execution_path": report["wiring"]["okx_execution_path"],
                            },
                            indent=2,
                        )
                    )
                    return 0
            if not current_message:
                raise RuntimeError("Smoke-injected opening message 9901/1 not found in snapshot")
            if current_message["status"] != "EXECUTED":
                raise RuntimeError(f"Expected EXECUTED status, got {current_message['status']}")
            if snapshot["config"]["trading"]["paused"]:
                raise RuntimeError("Runtime auto-paused during OKX demo smoke")
            if "reverse_to_long" not in report["wiring"]["configured_okx_supported_actions"]:
                raise RuntimeError("Expected reverse_to_long support in configured OKX demo path")

            runtime.inject_message(
                text=f"REVERSE SHORT {args.symbol} SIZE {args.size}",
                chat_id="-1000000000000",
                message_id=9902,
                event_type="edit",
                version=2,
                use_configured_okx_path=True,
            )
            reversed_snapshot = runtime.snapshot()
            reversed_message = _find_message(reversed_snapshot, 9902, 2)
            if reversed_message and reversed_message["status"] == "EXECUTION_FAILED":
                detail = reversed_snapshot["health"]["okx_rest"]["detail"]
                if _is_network_blocked(detail):
                    print(
                        json.dumps(
                            {
                                "status": "skipped",
                                "reason": detail,
                                "okx_execution_path": report["wiring"]["okx_execution_path"],
                            },
                            indent=2,
                        )
                    )
                    return 0
            if not reversed_message:
                raise RuntimeError("Smoke-injected reverse message 9902/2 not found in snapshot")
            if reversed_message["status"] != "EXECUTED":
                raise RuntimeError(
                    f"Expected reverse EXECUTED status, got {reversed_message['status']}"
                )
            short_positions = [pos for pos in reversed_snapshot["positions"] if pos["payload"]["side"] == "short"]
            if not short_positions:
                raise RuntimeError("Expected reverse smoke to move the local expected position to short")

            symbol = reversed_snapshot["orders"][0]["symbol"]
            closed = runtime.close_positions(symbol)
            post_close = runtime.snapshot()
            position = post_close["positions"][0]["payload"]
            if position["qty"] != 0.0 or position["side"] != "flat":
                raise RuntimeError("Expected demo smoke close to flatten the position")

            print(
                json.dumps(
                    {
                        "verify_status": report["status"],
                        "okx_execution_path": report["wiring"]["okx_execution_path"],
                        "order_status": snapshot["orders"][0]["status"],
                        "reverse_status": reversed_snapshot["orders"][0]["status"],
                        "exchange_order_id_present": bool(snapshot["orders"][0]["exchange_order_id"]),
                        "reverse_exchange_order_id_present": bool(reversed_snapshot["orders"][0]["exchange_order_id"]),
                        "closed_status": closed["closed"][0]["status"],
                        "position_flattened": True,
                    },
                    indent=2,
                )
            )
            return 0
        finally:
            runtime.stop()

def _find_message(snapshot: dict, message_id: int, version: int) -> dict | None:
    for item in snapshot.get("messages", []):
        if int(item.get("message_id", -1)) == int(message_id) and int(item.get("version", -1)) == int(version):
            return item
    return None


def _is_network_blocked(detail: str) -> bool:
    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            "temporary failure in name resolution",
            "name or service not known",
            "network is unreachable",
            "connection timed out",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
