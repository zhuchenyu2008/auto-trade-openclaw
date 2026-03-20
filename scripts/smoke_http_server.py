#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

from smoke_utils import mirror_source_local_env

PIN_HASH = "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92"


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
        try:
            port = _pick_free_port()
        except PermissionError:
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "local socket creation is blocked in this sandbox",
                    },
                    indent=2,
                )
            )
            return 0
        config = json.loads(json.dumps(source_config))
        config["web"]["port"] = port
        config["web"]["pin_hash"] = PIN_HASH
        config["runtime"]["data_dir"] = str(tmp_path / "runtime")
        config["runtime"]["sqlite_path"] = str(tmp_path / "runtime" / "app.db")
        config["trading"]["mode"] = "demo"
        config["trading"]["execution_mode"] = "automatic"
        config["trading"]["paused"] = False
        config["ai"]["provider"] = "heuristic"
        config["telegram"]["bot_token"] = ""
        config["okx"]["enabled"] = False
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        mirror_source_local_env(source_path, source_config, tmp_path)

        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "tg_okx_auto_trade.main", "serve", "--config", str(config_path)],
                cwd=root,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except PermissionError:
            print(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "binding a local HTTP port is blocked in this sandbox",
                    },
                    indent=2,
                )
            )
            return 0
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_for_server(opener, f"{base_url}/healthz")
            state = _request_json(opener, f"{base_url}/api/state", expect_status=401)
            if state["error"] != "Unauthorized":
                raise RuntimeError("Expected /api/state to require authentication")

            login_request = urllib.request.Request(
                f"{base_url}/login",
                data=urllib.parse.urlencode({"pin": "123456"}).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            opener.open(login_request, timeout=10).read()

            state = _request_json(opener, f"{base_url}/api/state")
            if state["config"]["okx"]["api_key"]:
                raise RuntimeError("Expected redacted OKX api_key in /api/state")
            if state["wiring"]["topic_target"] != "-1003720752566:topic:2080":
                raise RuntimeError("Expected operator topic target to be wired in HTTP smoke")
            if state["wiring"]["web_bind"] != f"127.0.0.1:{port}":
                raise RuntimeError("Expected runtime wiring to expose the active HTTP bind")
            if state["run_paths"]["web_login"] != f"http://127.0.0.1:{port}/login":
                raise RuntimeError("Expected run paths to use the active HTTP bind")
            if state["run_paths"]["topic_target_link"] != "https://t.me/c/3720752566/2080":
                raise RuntimeError("Expected operator topic link in HTTP smoke")
            if state["capabilities"]["demo_only_guard"]["status"] != "locked":
                raise RuntimeError("Expected demo-only guard capability to remain locked in HTTP smoke")
            if not state.get("remaining_gaps"):
                raise RuntimeError("Expected remaining_gaps in HTTP smoke")
            if not state.get("next_steps"):
                raise RuntimeError("Expected next_steps in HTTP smoke")

            injected = _request_json(
                opener,
                f"{base_url}/api/inject-message",
                data={
                    "text": "LONG ADAUSDT $1",
                    "chat_id": "-1000000000000",
                    "message_id": 7001,
                    "event_type": "new",
                },
                method="POST",
                expect_status=201,
            )
            if injected["orders"][0]["status"] != "filled":
                raise RuntimeError("Expected injected order to be filled in HTTP smoke")

            closed = _request_json(
                opener,
                f"{base_url}/api/positions/close",
                data={},
                method="POST",
                expect_status=201,
            )
            if closed["closed"][0]["status"] != "filled":
                raise RuntimeError("Expected close-all to succeed in HTTP smoke")

            ready = _request_json(opener, f"{base_url}/readyz")
            if ready["status"] == "error":
                raise RuntimeError("Expected /readyz to avoid error status in HTTP smoke")

            print(
                json.dumps(
                    {
                        "http_server": "ok",
                        "port": port,
                        "state_secret_redaction": True,
                        "web_bind": state["wiring"]["web_bind"],
                        "topic_target_link": state["run_paths"]["topic_target_link"],
                        "demo_only_guard": state["capabilities"]["demo_only_guard"]["status"],
                        "verification_status": state["verification_status"],
                        "next_steps_count": len(state["next_steps"]),
                        "inject_order_status": injected["orders"][0]["status"],
                        "close_status": closed["closed"][0]["status"],
                        "readyz_status": ready["status"],
                    },
                    indent=2,
                )
            )
            return 0
        finally:
            _stop_process(process)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(opener, url: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            opener.open(url, timeout=2).read()
            return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}")


def _request_json(opener, url: str, data: dict | None = None, method: str = "GET", expect_status: int = 200) -> dict:
    body = None if data is None else json.dumps(data).encode("utf-8")
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=10) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    if status != expect_status:
        raise RuntimeError(f"Expected HTTP {expect_status} from {url}, got {status}: {payload}")
    return payload


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
