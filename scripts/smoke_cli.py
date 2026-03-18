#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from smoke_utils import mirror_source_local_env


def _run(root: Path, env: dict[str, str], *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "tg_okx_auto_trade.main", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(args)}")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.demo.local.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env["TG_OKX_DISABLE_TOPIC_SEND"] = "1"
    source_path = Path(args.config)
    if not source_path.is_absolute():
        source_path = (root / source_path).resolve()
    source_config = json.loads(source_path.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = json.loads(json.dumps(source_config))
        config["runtime"]["data_dir"] = str(tmp_path / "runtime")
        config["runtime"]["sqlite_path"] = str(tmp_path / "runtime" / "app.db")
        config["ai"]["provider"] = "heuristic"
        config["telegram"]["bot_token"] = ""
        config["okx"]["enabled"] = False
        config["trading"]["mode"] = "demo"
        config["trading"]["execution_mode"] = "automatic"
        config["trading"]["paused"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        paths = _run(root, env, "paths", "--config", str(config_path))
        if paths["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
            raise RuntimeError("expected normalized operator topic link in CLI paths")
        if "paths_command" not in paths["run_paths"]:
            raise RuntimeError("expected paths_command in CLI direct-use output")
        if "direct_use_command" not in paths["run_paths"]:
            raise RuntimeError("expected direct_use_command in CLI direct-use output")
        if paths["activation_summary"]["manual_demo"]["status"] != "ready":
            raise RuntimeError("expected CLI paths output to mark manual demo as ready")
        if not paths["run_paths"]["runtime_direct_use_json"].endswith("/runtime/direct-use.json"):
            raise RuntimeError("expected runtime direct-use artifact path in CLI paths output")
        if not paths["run_paths"]["runtime_direct_use_text"].endswith("/runtime/direct-use.txt"):
            raise RuntimeError("expected runtime direct-use text artifact path in CLI paths output")
        if "close_all" not in paths["run_paths"]["configured_okx_supported_actions"]:
            raise RuntimeError("expected OKX action coverage in CLI paths output")
        if "set-topic-target" not in paths["run_paths"]["set_topic_target_command"]:
            raise RuntimeError("expected set-topic-target helper in CLI paths output")
        if "upsert-channel" not in paths["run_paths"]["upsert_channel_command"]:
            raise RuntimeError("expected upsert-channel helper in CLI paths output")

        snapshot = _run(root, env, "snapshot", "--config", str(config_path))
        if snapshot["config"]["web"]["pin_hash"]:
            raise RuntimeError("expected redacted web.pin_hash in snapshot command")
        direct_use_text = subprocess.run(
            [sys.executable, "-m", "tg_okx_auto_trade.main", "direct-use", "--config", str(config_path)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if direct_use_text.returncode != 0:
            raise RuntimeError(direct_use_text.stderr.strip() or "direct-use command failed")
        if "TG OKX Auto Trade Direct-Use Summary" not in direct_use_text.stdout:
            raise RuntimeError("expected direct-use text summary header in CLI smoke")

        topic = _run(
            root,
            env,
            "set-topic-target",
            "--config",
            str(config_path),
            "--target",
            "https://t.me/c/3720752566/2080",
        )
        if topic["target"] != "-1003720752566:topic:2080":
            raise RuntimeError("expected CLI topic helper to normalize the operator topic link")

        channel = _run(
            root,
            env,
            "upsert-channel",
            "--config",
            str(config_path),
            "--name",
            "CLI Channel",
            "--chat-id",
            "https://t.me/c/3720752566/2080",
        )
        if channel["channel"]["chat_id"] != "-1003720752566":
            raise RuntimeError("expected CLI channel helper to normalize chat links")

        disabled_channel = _run(
            root,
            env,
            "set-channel-enabled",
            "--config",
            str(config_path),
            "--channel-id",
            channel["channel"]["id"],
            "--disabled",
        )
        if disabled_channel["channel"]["enabled"] is not False:
            raise RuntimeError("expected set-channel-enabled to disable the CLI-created channel")

        removed_channel = _run(
            root,
            env,
            "remove-channel",
            "--config",
            str(config_path),
            "--channel-id",
            channel["channel"]["id"],
        )
        if removed_channel["status"] != "ok":
            raise RuntimeError("expected remove-channel to delete the CLI-created channel")

        _run(
            root,
            env,
            "inject-message",
            "--config",
            str(config_path),
            "--text",
            "LONG BTCUSDT SIZE 1",
            "--chat-id",
            "-1000000000000",
            "--message-id",
            "9101",
        )

        paused = _run(root, env, "pause", "--config", str(config_path), "--reason", "CLI smoke pause")
        if not paused["operator_state"]["paused"]:
            raise RuntimeError("expected pause command to persist paused state")

        resumed = _run(root, env, "resume", "--config", str(config_path), "--reason", "CLI smoke resume")
        if resumed["operator_state"]["paused"]:
            raise RuntimeError("expected resume command to clear paused state")

        reconcile = _run(root, env, "reconcile", "--config", str(config_path))
        if reconcile["status"] != "ok":
            raise RuntimeError(f"expected reconcile status ok, got {reconcile['status']}")

        topic_test = _run(root, env, "topic-test", "--config", str(config_path))
        if topic_test["status"] != "disabled":
            raise RuntimeError("expected topic-test to respect TG_OKX_DISABLE_TOPIC_SEND=1")

        operator_status = _run(root, env, "operator-command", "--config", str(config_path), "--text", "/status")
        if operator_status["command"] != "status":
            raise RuntimeError("expected operator-command /status to be handled")

        closed = _run(root, env, "close-positions", "--config", str(config_path), "--symbol", "BTC-USDT-SWAP")
        if closed["closed"][0]["status"] != "filled":
            raise RuntimeError("expected close-positions to flatten the simulated position")

        reset = _run(root, env, "reset-local-state", "--config", str(config_path))
        if reset["status"] != "ok":
            raise RuntimeError("expected reset-local-state to succeed")
        if reset["snapshot"]["orders"] or reset["snapshot"]["messages"] or reset["snapshot"]["positions"]:
            raise RuntimeError("expected reset-local-state to clear local runtime history")

        print(
            json.dumps(
                {
                    "snapshot_redaction": True,
                    "paths_status": paths["status"],
                    "paths_manual_demo": paths["activation_summary"]["manual_demo"]["status"],
                    "direct_use_text": True,
                    "topic_target_link": snapshot["run_paths"]["topic_target_link"],
                    "runtime_direct_use_json": paths["run_paths"]["runtime_direct_use_json"],
                    "runtime_direct_use_text": paths["run_paths"]["runtime_direct_use_text"],
                    "topic_target": topic["target"],
                    "channel_id": channel["channel"]["id"],
                    "channel_removed": removed_channel["status"] == "ok",
                    "paused_after_pause": paused["operator_state"]["paused"],
                    "paused_after_resume": resumed["operator_state"]["paused"],
                    "reconcile_status": reconcile["status"],
                    "topic_status": topic_test["status"],
                    "operator_status": operator_status["status"],
                    "close_status": closed["closed"][0]["status"],
                    "reset_status": reset["status"],
                },
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
