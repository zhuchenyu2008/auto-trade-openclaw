#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
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

    os.environ["TG_OKX_DISABLE_TOPIC_SEND"] = "1"
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
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        runtime = Runtime(config_path)
        runtime.start(background=False)
        try:
            runtime.update_config(
                {
                    "telegram": {
                        "operator_target": "https://t.me/c/3720752566/2080",
                    },
                    "web": {
                        "port": 6012,
                    },
                }
            )
            linked_channel = runtime.upsert_channel(
                {
                    "name": "Linked Source",
                    "source_type": "bot_api",
                    "chat_id": "https://t.me/c/3720752566/2080",
                    "enabled": True,
                }
            )
            named_channel = runtime.upsert_channel(
                {
                    "name": "Named Source",
                    "source_type": "bot_api",
                    "channel_username": "https://t.me/Vip_BTC",
                    "enabled": False,
                }
            )
            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            runtime.stop()

            reloaded = Runtime(config_path)
            try:
                report = reloaded.public_verification_report()
                if report["run_paths"]["configured_web_login"] != "http://127.0.0.1:6012/login":
                    raise RuntimeError("expected configured web login to use the persisted port")
                if report["wiring"]["topic_target"] != "-1003720752566:topic:2080":
                    raise RuntimeError("expected operator target link to persist in internal topic form")
                if report["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
                    raise RuntimeError("expected normalized operator topic link in usage paths")
                if report["snapshot"]["config"]["telegram"]["operator_thread_id"] != 2080:
                    raise RuntimeError("expected operator thread id to stay aligned with the normalized topic target")
                if report["snapshot"]["config"]["telegram"]["channels"][0]["chat_id"] != "-1003720752566":
                    raise RuntimeError("expected chat link channel to persist as an internal chat id")
                if report["snapshot"]["config"]["telegram"]["channels"][1]["channel_username"] != "Vip_BTC":
                    raise RuntimeError("expected username link channel to persist as a normalized username")
                if not Path(report["run_paths"]["runtime_direct_use_json"]).is_file():
                    raise RuntimeError("expected runtime direct-use artifact to exist after reload")

                print(
                    json.dumps(
                        {
                            "persisted_topic_target": persisted["telegram"]["operator_target"],
                            "persisted_web_port": persisted["web"]["port"],
                            "persisted_operator_thread_id": report["snapshot"]["config"]["telegram"]["operator_thread_id"],
                            "linked_channel_id": linked_channel["id"],
                            "linked_channel_chat_id": linked_channel["chat_id"],
                            "named_channel_id": named_channel["id"],
                            "named_channel_username": named_channel["channel_username"],
                            "configured_web_login": report["run_paths"]["configured_web_login"],
                            "topic_target_link": report["run_paths"]["topic_target_link"],
                        },
                        indent=2,
                    )
                )
                return 0
            finally:
                reloaded.stop()
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
