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
        config["trading"]["mode"] = "demo"
        config["trading"]["execution_mode"] = "automatic"
        config["trading"]["paused"] = False
        config["ai"]["provider"] = "heuristic"
        config["telegram"]["bot_token"] = ""
        config["telegram"]["channels"] = []
        config["okx"]["enabled"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)
        runtime = Runtime(config_path)
        runtime.start(background=False)
        try:
            controller = WebController(runtime)

            status, _, _ = controller.route("GET", "/login")
            if status != 200:
                raise RuntimeError("GET /login did not return 200")

            status, headers, _ = controller.route(
                "POST",
                "/login",
                body=b"pin=123456",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if status != 303:
                raise RuntimeError("POST /login did not succeed")
            session_cookie = headers["Set-Cookie"]

            status, _, state = controller.route("GET", "/api/state", headers={"Cookie": session_cookie})
            if status != 200:
                raise RuntimeError("GET /api/state did not return 200")
            if state["config"]["okx"]["api_key"]:
                raise RuntimeError("Expected redacted OKX api_key in /api/state")
            if not state["secret_status"]["okx_demo_credentials_configured"]:
                raise RuntimeError("Expected OKX credential presence to be detected from config.demo.local.json")
            if state["wiring"]["topic_target"] != "-1003720752566:topic:2080":
                raise RuntimeError("Expected operator topic target to be wired")
            if state["wiring"]["topic_target_source"] != "operator_target":
                raise RuntimeError("Expected operator_target to drive topic routing in smoke state")
            if state["config"]["telegram"]["operator_thread_id"] != 2080:
                raise RuntimeError("Expected operator thread id to align with the configured topic target")
            if "close_all" not in state["wiring"]["configured_okx_supported_actions"]:
                raise RuntimeError("Expected configured OKX action coverage in smoke state")
            if state["run_paths"]["web_bind"] != "127.0.0.1:6010":
                raise RuntimeError("Expected default configured web bind in smoke state")
            if state["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
                raise RuntimeError("Expected operator topic link in smoke state")
            if not state["run_paths"]["runtime_direct_use_json"].endswith("/runtime/direct-use.json"):
                raise RuntimeError("Expected runtime direct-use artifact path in smoke state")
            if state["capabilities"]["demo_only_guard"]["status"] != "locked":
                raise RuntimeError("Expected demo-only guard capability to remain locked")
            if state["capabilities"]["current_operating_profile"]["status"] != "manual_ready":
                raise RuntimeError("Expected current operating profile to reflect manual-demo direct-use readiness")
            if state["capabilities"]["telegram_ingestion"]["status"] != "blocked":
                raise RuntimeError("Expected Telegram ingestion capability to be blocked without bot wiring")
            if state["activation_summary"]["manual_demo"]["status"] != "ready":
                raise RuntimeError("Expected activation_summary.manual_demo to be ready")
            if state["activation_summary"]["automatic_telegram"]["status"] != "blocked":
                raise RuntimeError("Expected activation_summary.automatic_telegram to stay blocked")
            if not state.get("remaining_gaps"):
                raise RuntimeError("Expected remaining_gaps in smoke state")
            if not state.get("next_steps"):
                raise RuntimeError("Expected next_steps in smoke state")
            if "TG OKX Auto Trade Direct-Use Summary" not in state.get("direct_use_text", ""):
                raise RuntimeError("Expected direct_use_text summary in smoke state")

            status, _, ai_updated = controller.route(
                "POST",
                "/api/config",
                body=json.dumps(
                    {
                        "ai": {
                            "provider": "heuristic",
                            "model": "scalp-v2",
                            "thinking": "medium",
                            "timeout_seconds": 11,
                            "system_prompt": "Return JSON only.",
                        }
                    }
                ).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 200 or ai_updated["config"]["ai"]["model"] != "scalp-v2":
                raise RuntimeError("Expected AI config patch to update runtime state")

            status, _, injected = controller.route(
                "POST",
                "/api/inject-message",
                body=json.dumps(
                    {
                        "text": "LONG ADAUSDT $1",
                        "chat_id": "-1000000000000",
                        "message_id": 9001,
                        "event_type": "new",
                    }
                ).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or injected["orders"][0]["status"] != "filled":
                raise RuntimeError("Expected simulated demo order to be filled")

            status, _, channel = controller.route(
                "POST",
                "/api/channels/upsert",
                body=json.dumps(
                    {
                        "name": "Smoke Channel",
                        "source_type": "bot_api",
                        "chat_id": "-100888",
                        "enabled": True,
                    }
                ).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or channel["id"] != "chan-888":
                raise RuntimeError("Expected channel id derived from chat_id")

            status, _, toggled = controller.route(
                "POST",
                "/api/channels/toggle",
                body=json.dumps({"channel_id": channel["id"], "enabled": False}).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 200 or toggled["enabled"] is not False:
                raise RuntimeError("Expected channel toggle endpoint to disable the channel")

            status, _, removed = controller.route(
                "POST",
                "/api/channels/remove",
                body=json.dumps({"channel_id": channel["id"]}).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 200 or removed["removed"] is not True:
                raise RuntimeError("Expected channel remove endpoint to delete the channel")

            status, _, reconcile = controller.route(
                "POST",
                "/api/actions/reconcile",
                body=b"{}",
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or reconcile["status"] not in {"ok", "skipped"}:
                raise RuntimeError("Expected reconcile endpoint to succeed")

            status, _, topic_test = controller.route(
                "POST",
                "/api/actions/topic-test",
                body=b"{}",
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or topic_test.get("target") != "-1003720752566:topic:2080":
                raise RuntimeError("Expected topic smoke to target the configured operator topic")

            status, _, operator_status = controller.route(
                "POST",
                "/api/actions/operator-command",
                body=json.dumps({"text": "/status"}).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or operator_status.get("command") != "status":
                raise RuntimeError("Expected operator command endpoint to handle /status")

            status, _, closed = controller.route(
                "POST",
                "/api/positions/close",
                body=b"{}",
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or closed["closed"][0]["status"] != "filled":
                raise RuntimeError("Expected manual close to use the simulated fill path")

            status, _, reset = controller.route(
                "POST",
                "/api/actions/reset-local-state",
                body=b"{}",
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
            if status != 201 or reset["status"] != "ok":
                raise RuntimeError("Expected reset-local-state endpoint to succeed")
            if reset["snapshot"]["orders"] or reset["snapshot"]["messages"] or reset["snapshot"]["positions"]:
                raise RuntimeError("Expected reset-local-state endpoint to clear local runtime state")

            status, _, ready = controller.route("GET", "/readyz")
            if status != 200 or ready["status"] == "error":
                raise RuntimeError("Expected /readyz to avoid error status in smoke run")

            print(
                json.dumps(
                    {
                        "login": "ok",
                        "state_secret_redaction": True,
                        "current_profile": state["capabilities"]["current_operating_profile"]["status"],
                        "manual_demo": state["activation_summary"]["manual_demo"]["status"],
                        "automatic_telegram": state["activation_summary"]["automatic_telegram"]["status"],
                        "direct_use_text": True,
                        "inject_order_status": injected["orders"][0]["status"],
                        "channel_id": channel["id"],
                        "channel_toggle_enabled": toggled["enabled"],
                        "channel_removed": removed["removed"],
                        "ai_model": ai_updated["config"]["ai"]["model"],
                        "ai_thinking": ai_updated["config"]["ai"]["thinking"],
                        "topic_target_source": state["wiring"]["topic_target_source"],
                        "topic_target_link": state["run_paths"]["topic_target_link"],
                        "operator_thread_id": state["config"]["telegram"]["operator_thread_id"],
                        "telegram_ingestion_status": state["capabilities"]["telegram_ingestion"]["status"],
                        "verification_status": state["verification_status"],
                        "next_steps_count": len(state["next_steps"]),
                        "web_bind": state["run_paths"]["web_bind"],
                        "reconcile_status": reconcile["status"],
                        "topic_sent": bool(topic_test.get("sent")),
                        "topic_reason": topic_test.get("reason") or topic_test.get("stderr") or "",
                        "operator_status": operator_status["status"],
                        "close_status": closed["closed"][0]["status"],
                        "reset_status": reset["status"],
                        "readyz_status": ready["status"],
                    },
                    indent=2,
                )
            )
            return 0
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
