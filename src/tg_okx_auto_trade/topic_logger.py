from __future__ import annotations

import os
import subprocess

from .config import AppConfig, resolve_topic_target


class TopicLogger:
    def __init__(self, config: AppConfig):
        self.config = config

    def target(self) -> str:
        return resolve_topic_target(self.config)

    def send(self, text: str) -> dict:
        target = self.target()
        thread_id = self.config.telegram.operator_thread_id
        if not target:
            return {
                "sent": False,
                "status": "missing_target",
                "reason": "telegram.report_topic or telegram.operator_target is not configured",
            }
        if os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() == "1":
            return {
                "sent": False,
                "status": "disabled",
                "reason": "Topic delivery disabled by TG_OKX_DISABLE_TOPIC_SEND=1",
                "target": target,
            }
        cmd = [
            "openclaw",
            "message",
            "send",
            "--channel",
            "telegram",
            "--target",
            target,
            "--message",
            text,
            "--json",
        ]
        if thread_id and ":topic:" not in target:
            cmd += ["--thread-id", str(thread_id)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as exc:
            return {
                "sent": False,
                "status": "failed",
                "reason": str(exc),
                "target": target,
            }
        return {
            "sent": result.returncode == 0,
            "status": "sent" if result.returncode == 0 else "failed",
            "target": target,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
