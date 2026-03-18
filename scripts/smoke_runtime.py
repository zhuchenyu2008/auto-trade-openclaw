#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

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
        config["runtime"]["config_reload_seconds"] = 1
        config["ai"]["provider"] = "heuristic"
        config["telegram"]["bot_token"] = ""
        config["okx"]["enabled"] = False
        config["trading"]["mode"] = "demo"
        config["trading"]["execution_mode"] = "automatic"
        config["trading"]["paused"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        runtime = Runtime(config_path)
        runtime.start(background=True)
        try:
            report = runtime.public_verification_report()
            if report["status"] == "error":
                raise RuntimeError("verify report returned error status")
            if report["run_paths"]["topic_target"] != "-1003720752566:topic:2080":
                raise RuntimeError("expected operator topic target wiring in run paths")
            if report["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
                raise RuntimeError("expected operator topic link in run paths")
            if report["run_paths"]["topic_target_source"] != "operator_target":
                raise RuntimeError("expected operator_target to be the active topic target source")
            if report["snapshot"]["config"]["telegram"]["operator_thread_id"] != 2080:
                raise RuntimeError("expected operator thread id to stay aligned with the topic target")
            if not Path(report["run_paths"]["runtime_direct_use_json"]).is_file():
                raise RuntimeError("expected runtime direct-use artifact to exist")
            if not Path(report["run_paths"]["runtime_direct_use_text"]).is_file():
                raise RuntimeError("expected runtime direct-use text artifact to exist")
            if not Path(report["run_paths"]["runtime_public_state_json"]).is_file():
                raise RuntimeError("expected runtime public-state artifact to exist")
            if "close_all" not in report["run_paths"]["configured_okx_supported_actions"]:
                raise RuntimeError("expected configured OKX action coverage in runtime paths")
            if report["snapshot"]["health"]["topic_logger"]["status"] != "disabled":
                raise RuntimeError("expected topic logger health to reflect TG_OKX_DISABLE_TOPIC_SEND=1")
            if report["capabilities"]["demo_only_guard"]["status"] != "locked":
                raise RuntimeError("expected demo-only guard capability to remain locked")
            if report["capabilities"]["current_operating_profile"]["status"] != "manual_ready":
                raise RuntimeError("expected current operating profile to reflect manual-demo direct-use readiness")
            if report["capabilities"]["operator_topic"]["status"] != "disabled":
                raise RuntimeError("expected operator topic capability to reflect disabled topic delivery")
            if report["activation_summary"]["manual_demo"]["status"] != "ready":
                raise RuntimeError("expected activation summary to mark manual demo path as ready")
            if report["activation_summary"]["automatic_telegram"]["status"] != "blocked":
                raise RuntimeError("expected activation summary to keep automatic Telegram blocked without bot wiring")
            if "remaining_gaps" not in report or not report["remaining_gaps"]:
                raise RuntimeError("expected remaining_gaps in runtime smoke report")
            if not report.get("next_steps"):
                raise RuntimeError("expected next_steps in runtime smoke report")

            updated = json.loads(config_path.read_text(encoding="utf-8"))
            updated["trading"]["default_leverage"] = 33
            updated["telegram"]["operator_target"] = "https://t.me/c/3720752566/2080"
            updated["web"]["port"] = 6011
            config_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")

            deadline = time.time() + 5
            while time.time() < deadline:
                snapshot = runtime.snapshot()
                if (
                    snapshot["config"]["trading"]["default_leverage"] == 33
                    and snapshot["config"]["telegram"]["operator_target"] == "-1003720752566:topic:2080"
                    and snapshot["config"]["telegram"]["operator_thread_id"] == 2080
                    and snapshot["config"]["web"]["port"] == 6011
                ):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("runtime did not hot-reload config changes")

            time.sleep(0.02)
            (tmp_path / ".env").write_text("TG_OKX_TELEGRAM_BOT_TOKEN=runtime-smoke-bot-token\n", encoding="utf-8")
            deadline = time.time() + 5
            while time.time() < deadline:
                refreshed = runtime.public_snapshot()
                if (
                    refreshed["secret_status"]["telegram_bot_token_configured"]
                    and refreshed["secret_sources"]["telegram_bot_token"] == "env"
                    and refreshed["wiring"]["operator_command_ingress"] == "ready"
                ):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("runtime did not hot-reload local .env bot token changes")

            runtime.inject_message("LONG BTCUSDT SIZE 2", "-1000000000000", 8801)
            snapshot = runtime.snapshot()
            if snapshot["messages"][0]["status"] != "EXECUTED":
                raise RuntimeError(f"expected EXECUTED message status, got {snapshot['messages'][0]['status']}")
            if snapshot["orders"][0]["payload"]["lever"] != 33:
                raise RuntimeError("expected hot-reloaded leverage to be used for injected demo order")

            topic_result = runtime.send_topic_test()
            if topic_result.get("status") != "disabled":
                raise RuntimeError("expected topic smoke to be disabled in smoke runtime")

            runtime.pause_trading("Smoke pause")
            if not runtime.snapshot()["operator_state"]["paused"]:
                raise RuntimeError("expected pause_trading to pause runtime")
            runtime.resume_trading("Smoke resume")
            if runtime.snapshot()["operator_state"]["paused"]:
                raise RuntimeError("expected resume_trading to clear pause")

            reconcile = runtime.reconcile_now()
            if reconcile["status"] != "ok":
                raise RuntimeError(f"expected reconcile_now ok, got {reconcile['status']}")

            final_snapshot = runtime.public_snapshot()
            direct_use = json.loads(Path(runtime.usage_paths()["runtime_direct_use_json"]).read_text(encoding="utf-8"))
            direct_use_text = Path(runtime.usage_paths()["runtime_direct_use_text"]).read_text(encoding="utf-8")
            public_state = json.loads(Path(runtime.usage_paths()["runtime_public_state_json"]).read_text(encoding="utf-8"))
            if direct_use["activation_summary"]["manual_demo"]["status"] != "ready":
                raise RuntimeError("expected direct-use artifact to retain activation_summary.manual_demo")
            if public_state["activation_summary"]["automatic_telegram"]["status"] != "blocked":
                raise RuntimeError("expected public-state artifact to retain activation_summary.automatic_telegram")
            if "TG OKX Auto Trade Direct-Use Summary" not in direct_use_text:
                raise RuntimeError("expected direct-use text artifact to contain the text summary header")
            if runtime.usage_paths()["web_login"] not in direct_use_text:
                raise RuntimeError("expected direct-use text artifact to include the active web login path")
            print(
                json.dumps(
                    {
                        "verify_status": report["status"],
                        "current_profile": report["capabilities"]["current_operating_profile"]["status"],
                        "manual_demo": report["activation_summary"]["manual_demo"]["status"],
                        "automatic_telegram": report["activation_summary"]["automatic_telegram"]["status"],
                        "run_paths_web": report["run_paths"]["web_login"],
                        "runtime_state_dir": report["run_paths"]["runtime_state_dir"],
                        "runtime_direct_use_json": report["run_paths"]["runtime_direct_use_json"],
                        "runtime_direct_use_text": report["run_paths"]["runtime_direct_use_text"],
                        "topic_target_link": report["run_paths"]["topic_target_link"],
                        "operator_thread_id": final_snapshot["config"]["telegram"]["operator_thread_id"],
                        "reloaded_bot_token": final_snapshot["secret_status"]["telegram_bot_token_configured"],
                        "bot_token_source": final_snapshot["secret_sources"]["telegram_bot_token"],
                        "operator_command_ingress": final_snapshot["wiring"]["operator_command_ingress"],
                        "hot_reload_leverage": final_snapshot["config"]["trading"]["default_leverage"],
                        "configured_web_port": final_snapshot["config"]["web"]["port"],
                        "demo_only_guard": report["capabilities"]["demo_only_guard"]["status"],
                        "web_restart_required": runtime.usage_paths()["web_restart_required"],
                        "artifact_web_login": direct_use["run_paths"]["web_login"],
                        "artifact_next_steps": len(direct_use["next_steps"]),
                        "artifact_manual_demo": direct_use["activation_summary"]["manual_demo"]["status"],
                        "public_state_verification_status": public_state["verification_status"],
                        "public_state_automatic_telegram": public_state["activation_summary"]["automatic_telegram"]["status"],
                        "inject_order_status": final_snapshot["orders"][0]["status"],
                        "topic_status": final_snapshot["health"]["topic_logger"]["status"],
                        "paused_after_resume": final_snapshot["operator_state"]["paused"],
                        "reconcile_status": reconcile["status"],
                    },
                    indent=2,
                )
            )
            return 0
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
