#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.demo.local.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env.setdefault("TG_OKX_WEB_PIN", "123456")
    source_path = Path(args.config)
    if not source_path.is_absolute():
        source_path = (root / source_path).resolve()
    verify_cmd = [
        sys.executable,
        "-m",
        "tg_okx_auto_trade.main",
        "verify",
        "--config",
        str(source_path),
    ]
    verify_result = subprocess.run(verify_cmd, capture_output=True, text=True, env=env)
    if verify_result.returncode != 0:
        print(verify_result.stderr, file=sys.stderr)
        return verify_result.returncode
    verify_payload = json.loads(verify_result.stdout)
    if verify_payload["status"] == "error":
        print("expected config.demo.local.json verify command to avoid error status", file=sys.stderr)
        return 1
    if verify_payload["snapshot"]["config"]["web"]["pin_hash"]:
        print("expected redacted verify output for web.pin_hash", file=sys.stderr)
        return 1
    if verify_payload["wiring"]["topic_target"] != "-1003720752566:topic:2080":
        print("expected operator topic target wiring in config.demo.local.json", file=sys.stderr)
        return 1
    if verify_payload["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
        print("expected operator topic link in run paths", file=sys.stderr)
        return 1
    if verify_payload["secret_status"]["okx_demo_credentials_configured"] is not True:
        print("expected OKX demo credentials to be present in config.demo.local.json", file=sys.stderr)
        return 1
    if verify_payload["capabilities"]["demo_only_guard"]["status"] != "locked":
        print("expected demo-only guard to remain locked", file=sys.stderr)
        return 1
    current_profile_status = verify_payload["capabilities"]["current_operating_profile"]["status"]
    if current_profile_status not in {"manual_ready", "attention"}:
        print("expected current operating profile to remain in a manual-demo-ready/attention state", file=sys.stderr)
        return 1
    if verify_payload["activation_summary"]["manual_demo"]["status"] != "ready":
        print("expected activation_summary.manual_demo to be ready", file=sys.stderr)
        return 1
    if verify_payload["activation_summary"]["automatic_telegram"]["status"] != "blocked":
        print("expected activation_summary.automatic_telegram to remain blocked without bot wiring", file=sys.stderr)
        return 1
    if "remaining_gaps" not in verify_payload:
        print("expected remaining_gaps in verify payload", file=sys.stderr)
        return 1
    runtime_state_dir = Path(verify_payload["run_paths"]["runtime_state_dir"])
    direct_use_json_path = Path(verify_payload["run_paths"]["runtime_direct_use_json"])
    direct_use_text_path = Path(verify_payload["run_paths"]["runtime_direct_use_text"])
    public_state_path = Path(verify_payload["run_paths"]["runtime_public_state_json"])
    if runtime_state_dir != (source_path.parent / "runtime" / "demo-local").resolve():
        print("expected config.demo.local.json runtime path to resolve into runtime/demo-local", file=sys.stderr)
        return 1
    for artifact_path in (direct_use_json_path, direct_use_text_path, public_state_path):
        if not artifact_path.is_file():
            print(f"expected runtime artifact to exist after verify: {artifact_path}", file=sys.stderr)
            return 1
    direct_use_payload = json.loads(direct_use_json_path.read_text(encoding="utf-8"))
    direct_use_text = direct_use_text_path.read_text(encoding="utf-8")
    public_state = json.loads(public_state_path.read_text(encoding="utf-8"))
    if direct_use_payload["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
        print("expected topic target link in direct-use artifact", file=sys.stderr)
        return 1
    direct_profile_status = direct_use_payload["capabilities"]["current_operating_profile"]["status"]
    if direct_profile_status not in {"manual_ready", "attention"}:
        print("expected direct-use artifact current profile to remain manual_ready/attention", file=sys.stderr)
        return 1
    if "profile_detail:" not in direct_use_text or "next_action:" not in direct_use_text:
        print("expected direct-use text artifact to expose profile detail and next action", file=sys.stderr)
        return 1
    if public_state["verification_status"] != verify_payload["status"]:
        print("expected public-state verification status to match verify output", file=sys.stderr)
        return 1
    summary = {
        "config_verify": {
            "status": verify_payload["status"],
            "current_profile": current_profile_status,
            "manual_demo": verify_payload["activation_summary"]["manual_demo"]["status"],
            "automatic_telegram": verify_payload["activation_summary"]["automatic_telegram"]["status"],
            "web_login": verify_payload["run_paths"]["web_login"],
            "runtime_state_dir": verify_payload["run_paths"]["runtime_state_dir"],
            "runtime_direct_use_json": verify_payload["run_paths"]["runtime_direct_use_json"],
            "runtime_public_state_json": verify_payload["run_paths"]["runtime_public_state_json"],
            "topic_target": verify_payload["wiring"]["topic_target"],
            "topic_target_link": verify_payload["run_paths"]["topic_target_link"],
            "okx_execution_path": verify_payload["wiring"]["okx_execution_path"],
            "telegram_ingestion_status": verify_payload["capabilities"]["telegram_ingestion"]["status"],
            "runtime_artifacts_ready": True,
        }
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = json.loads(source_path.read_text(encoding="utf-8"))
        config["runtime"]["data_dir"] = str(tmp_path / "data")
        config["runtime"]["sqlite_path"] = str(tmp_path / "data" / "app.db")
        config["trading"]["paused"] = False
        config["telegram"]["bot_token"] = ""
        config["telegram"]["channels"] = []
        config["telegram"]["report_topic"] = ""
        config["telegram"]["operator_target"] = ""
        config["okx"]["enabled"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "tg_okx_auto_trade.main",
            "inject-message",
            "--config",
            str(config_path),
            "--chat-id",
            "-1000000000000",
            "--message-id",
            "101",
            "--text",
            "LONG BTCUSDT now",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return result.returncode
        snapshot = json.loads(result.stdout)
        if snapshot["config"]["trading"]["mode"] != "demo":
            print("expected demo mode in safe local smoke clone", file=sys.stderr)
            return 1
        if snapshot["config"]["web"]["pin_hash"]:
            print("expected redacted inject output for web.pin_hash", file=sys.stderr)
            return 1
        if not snapshot["orders"]:
            print("expected at least one simulated order in safe local smoke clone", file=sys.stderr)
            return 1
        if snapshot["orders"][0]["payload"]["lever"] != 20:
            print("expected default leverage 20 in safe local smoke clone", file=sys.stderr)
            return 1
        summary["safe_local_smoke"] = {
            "message_status": snapshot["messages"][0]["status"],
            "order_status": snapshot["orders"][0]["status"],
            "execution_path": snapshot["wiring"]["okx_execution_path"],
            "positions_count": snapshot["dashboard"]["positions_count"],
        }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
