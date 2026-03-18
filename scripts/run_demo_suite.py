#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.demo.local.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    base_env = os.environ.copy()
    # Keep the generic suite reproducible even if the parent shell exported
    # topic-send suppression for manual smoke work.
    base_env.pop("TG_OKX_DISABLE_TOPIC_SEND", None)
    base_env["PYTHONPATH"] = str(root / "src")
    smoke_env = dict(base_env)
    smoke_env["TG_OKX_DISABLE_TOPIC_SEND"] = "1"

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (root / config_path).resolve()

    steps = [
        {
            "name": "unit_tests",
            "cmd": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            "env": base_env,
        },
        {
            "name": "verify_demo",
            "cmd": [sys.executable, "scripts/verify_demo.py", "--config", str(config_path)],
            "env": base_env,
        },
        {
            "name": "smoke_config",
            "cmd": [sys.executable, "scripts/smoke_config.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_cli",
            "cmd": [sys.executable, "scripts/smoke_cli.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_runtime",
            "cmd": [sys.executable, "scripts/smoke_runtime.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_e2e",
            "cmd": [sys.executable, "scripts/smoke_e2e.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_web",
            "cmd": [sys.executable, "scripts/smoke_web.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_operator",
            "cmd": [sys.executable, "scripts/smoke_operator.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_telegram",
            "cmd": [sys.executable, "scripts/smoke_telegram.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_http_server",
            "cmd": [sys.executable, "scripts/smoke_http_server.py", "--config", str(config_path)],
            "env": smoke_env,
        },
        {
            "name": "smoke_okx_demo",
            "cmd": [sys.executable, "scripts/smoke_okx_demo.py", "--config", str(config_path)],
            "env": smoke_env,
        },
    ]

    results: list[dict[str, object]] = []
    failed = False
    passed_count = 0
    skipped_count = 0
    for step in steps:
        result = subprocess.run(
            step["cmd"],
            cwd=root,
            env=step["env"],
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        payload = _parse_json(stdout)
        status = "passed" if result.returncode == 0 else "failed"
        reason = ""
        if isinstance(payload, dict) and payload.get("status") == "skipped":
            status = "skipped"
            reason = str(payload.get("reason", "skipped"))
            skipped_count += 1
        elif result.returncode != 0:
            reason = stderr or stdout or "command failed"
            failed = True
        else:
            passed_count += 1
        results.append(
            {
                "name": step["name"],
                "status": status,
                "reason": reason,
                "summary": payload if payload is not None else _summarize_text(stdout or stderr),
            }
        )

    print(
        json.dumps(
            {
                "suite_status": "failed" if failed else "passed",
                "suite_scope": "demo_only",
                "live_trading_tested": False,
                "config": str(config_path),
                "passed_count": passed_count,
                "skipped_count": skipped_count,
                "failed_count": len(steps) - passed_count - skipped_count,
                "results": results,
            },
            indent=2,
        )
    )
    return 1 if failed else 0


def _parse_json(text: str) -> dict[str, object] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [index for index, char in enumerate(text) if char == "{"]
    for index in reversed(starts):
        candidate = text[index:].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _summarize_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    return lines[-2] if lines[-1] == "OK" else lines[-1]


if __name__ == "__main__":
    raise SystemExit(main())
