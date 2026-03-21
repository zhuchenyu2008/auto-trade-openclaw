import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import io
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tg_okx_auto_trade.ai import OpenClawAI
from tg_okx_auto_trade.config import hash_pin, local_env_path, read_env_file
from tg_okx_auto_trade.models import NormalizedMessage, TradingIntent
from tg_okx_auto_trade.runtime import (
    DEFAULT_DEMO_SIGNAL_TEXT,
    Runtime,
)
from tg_okx_auto_trade.telegram import parse_public_channel_html
from tg_okx_auto_trade.web import WebController


CONFIG_TEMPLATE = {
    "web": {"host": "127.0.0.1", "port": 6010, "pin_hash": hash_pin("123456"), "pin_plaintext_env": "TG_OKX_WEB_PIN"},
    "runtime": {"data_dir": "data", "sqlite_path": "data/app.db", "log_retention_days": 14, "config_reload_seconds": 1},
    "trading": {
        "mode": "demo",
        "execution_mode": "automatic",
        "default_leverage": 20,
        "margin_mode": "isolated",
        "position_mode": "net",
        "paper_trading_enabled": True,
        "live_trading_enabled": False,
        "global_tp_sl_enabled": False,
        "global_take_profit_ratio": 50.0,
        "global_stop_loss_ratio": 20.0,
        "allow_live_switch": False,
        "readonly_close_only": False,
        "paused": False,
    },
    "ai": {"provider": "heuristic", "model": "default", "openclaw_agent_id": "main", "thinking": "high", "timeout_seconds": 5, "system_prompt": "json"},
    "telegram": {
        "bot_token": "",
        "bot_token_env": "TG_OKX_TELEGRAM_BOT_TOKEN",
        "poll_interval_seconds": 1,
        "channels": [{
            "id": "test",
            "name": "Test",
            "source_type": "bot_api",
            "chat_id": "-1001",
            "channel_username": "",
            "enabled": True,
            "priority": 100,
            "parse_profile_id": "default",
            "strategy_profile_id": "default",
            "risk_profile_id": "default",
            "paper_trading_enabled": True,
            "live_trading_enabled": False,
            "listen_new_messages": True,
            "listen_edits": True,
            "listen_deletes": False,
            "reconcile_interval_seconds": 30,
            "dedup_window_seconds": 3600,
            "notes": ""
        }],
        "report_topic": "",
        "operator_target": "",
        "operator_thread_id": 0
    },
    "okx": {
        "enabled": False,
        "api_key": "",
        "api_secret": "",
        "passphrase": "",
        "api_key_env": "TG_OKX_OKX_API_KEY",
        "api_secret_env": "TG_OKX_OKX_API_SECRET",
        "passphrase_env": "TG_OKX_OKX_PASSPHRASE",
        "use_demo": True,
        "rest_base": "https://www.okx.com",
        "ws_private_url": ""
    }
}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        os.chdir(self.root)
        data_dir = self.root / "data"
        data_dir.mkdir()
        config = json.loads(json.dumps(CONFIG_TEMPLATE))
        config["runtime"]["data_dir"] = str(data_dir)
        config["runtime"]["sqlite_path"] = str(data_dir / "app.db")
        (self.root / "config.example.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        (self.root / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.runtime = Runtime(self.root / "config.json")

    def tearDown(self):
        if getattr(self, "runtime", None):
            self.runtime.stop()
        os.chdir(self.original_cwd)
        self.tempdir.cleanup()

    def _message(self, text, version=1):
        return NormalizedMessage(
            source="telegram",
            adapter="manual",
            chat_id="-1001",
            message_id=1,
            event_type="new" if version == 1 else "edit",
            version=version,
            date="2026-03-17T00:00:00+00:00",
            edit_date=None,
            text=text,
            caption="",
            media=[],
            entities=[],
            reply_to=None,
            forward_from=None,
            raw_hash=f"raw-{version}",
            semantic_hash=f"sem-{version}",
        )

    def _telegram_message(self, *, message_id=1, date=100, edit_date=None, text="LONG BTCUSDT"):
        return {
            "message_id": message_id,
            "date": date,
            "edit_date": edit_date,
            "text": text,
            "chat": {"id": -1001, "username": "testchan"},
        }

    def _operator_telegram_message(self, *, message_id=1, date=100, edit_date=None, text="/status", thread_id=2080):
        return {
            "message_id": message_id,
            "date": date,
            "edit_date": edit_date,
            "text": text,
            "message_thread_id": thread_id,
            "chat": {"id": -1003720752566, "username": "smallclaw"},
        }

    def _public_web_channel(self, *, channel_username="lbeobhpreo", enabled=True):
        return {
            "id": channel_username,
            "name": f"Public {channel_username}",
            "source_type": "public_web",
            "chat_id": "",
            "channel_username": channel_username,
            "enabled": enabled,
            "priority": 100,
            "parse_profile_id": "default",
            "strategy_profile_id": "default",
            "risk_profile_id": "default",
            "paper_trading_enabled": True,
            "live_trading_enabled": False,
            "listen_new_messages": True,
            "listen_edits": True,
            "listen_deletes": False,
            "reconcile_interval_seconds": 30,
            "dedup_window_seconds": 3600,
            "notes": "",
        }

    def test_default_leverage_is_20(self):
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        order = self.runtime.storage.latest_orders(1)[0]
        self.assertEqual(order["payload"]["lever"], 20)

    def test_duplicate_message_version_does_not_duplicate_order(self):
        msg = self._message("LONG BTCUSDT")
        self.runtime.process_message(msg)
        self.runtime.process_message(msg)
        self.assertEqual(len(self.runtime.storage.latest_orders(10)), 1)

    def test_edit_creates_new_version_without_duplicate_block(self):
        self.runtime.process_message(self._message("LONG BTCUSDT", version=1))
        self.runtime.process_message(self._message("SHORT BTCUSDT", version=2))
        self.assertEqual(len(self.runtime.storage.latest_orders(10)), 2)

    def test_global_tp_sl_disabled_by_default(self):
        snapshot = self.runtime.snapshot()
        self.assertFalse(snapshot["config"]["trading"]["global_tp_sl_enabled"])

    def test_default_demo_signal_text_uses_explicit_amount_sample(self):
        self.assertEqual(DEFAULT_DEMO_SIGNAL_TEXT, "LONG ADAUSDT $1")
        intent = self.runtime.ai.parse(self._message(DEFAULT_DEMO_SIGNAL_TEXT), [], {})
        self.assertEqual(intent.symbol, "ADA-USDT-SWAP")
        self.assertEqual(intent.size_value, 1.0)
        self.assertFalse(intent.require_manual_confirmation)

    def test_snapshot_exposes_recent_pipeline_state(self):
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.snapshot()
        self.assertTrue(snapshot["messages"])
        self.assertTrue(snapshot["ai_decisions"])
        self.assertEqual(snapshot["messages"][0]["status"], "EXECUTED")
        self.assertEqual(snapshot["ai_decisions"][0]["payload"]["action"], "open_long")

    def test_authentication_works(self):
        session = self.runtime.authenticate("123456")
        self.assertTrue(session)
        self.assertTrue(self.runtime.check_session(session))

    def test_config_update_persists_to_disk(self):
        updated = self.runtime.update_config({"trading": {"default_leverage": 33}})
        payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(updated.trading.default_leverage, 33)
        self.assertEqual(payload["trading"]["default_leverage"], 33)

    def test_config_update_persists_ai_settings(self):
        updated = self.runtime.update_config(
            {
                "ai": {
                    "provider": "heuristic",
                    "model": "swing-v2",
                    "thinking": "medium",
                    "timeout_seconds": 12,
                    "system_prompt": "return strict json",
                }
            }
        )
        payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(updated.ai.model, "swing-v2")
        self.assertEqual(updated.ai.thinking, "medium")
        self.assertEqual(updated.ai.timeout_seconds, 12)
        self.assertEqual(payload["ai"]["system_prompt"], "return strict json")

    def test_config_update_preserves_simulated_positions(self):
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        self.runtime.update_config({"trading": {"default_leverage": 21}})
        positions = self.runtime.snapshot()["positions"]
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["payload"]["side"], "long")

    def test_simulated_positions_are_restored_after_restart(self):
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        reloaded = Runtime(self.root / "config.json")
        self.addCleanup(reloaded.stop)
        self.assertEqual(len(reloaded.okx.positions()), 1)
        self.assertEqual(reloaded.okx.positions()[0]["side"], "long")

    def test_runtime_paths_resolve_from_config_location(self):
        config_dir = self.root / "instance"
        config_dir.mkdir()
        config = json.loads(json.dumps(CONFIG_TEMPLATE))
        config["runtime"]["data_dir"] = "runtime-data"
        config["runtime"]["sqlite_path"] = "runtime-data/app.db"
        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        resolved = Runtime(config_path)
        self.addCleanup(resolved.stop)
        snapshot = resolved.snapshot()
        self.assertEqual(snapshot["config"]["runtime"]["data_dir"], str(config_dir / "runtime-data"))
        self.assertEqual(snapshot["config"]["runtime"]["sqlite_path"], str(config_dir / "runtime-data" / "app.db"))

    def test_verify_command_runs_from_repo_root(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["TG_OKX_WEB_PIN"] = "123456"
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["telegram"]["bot_token"] = "bot-secret"
        config_payload["okx"]["api_key"] = "api-key"
        config_payload["okx"]["api_secret"] = "api-secret"
        config_payload["okx"]["passphrase"] = "passphrase"
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "verify",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(payload["status"], {"ok", "warn"})
        checks = {item["name"]: item["status"] for item in payload["checks"]}
        self.assertEqual(checks["demo_only_guard"], "pass")
        self.assertIn("run_paths", payload)
        self.assertTrue(payload["run_paths"]["serve_command"].startswith("python3 -m tg_okx_auto_trade.main serve"))
        self.assertEqual(payload["snapshot"]["config"]["telegram"]["bot_token"], "")
        self.assertEqual(payload["snapshot"]["config"]["okx"]["api_key"], "")
        self.assertEqual(payload["snapshot"]["config"]["web"]["pin_hash"], "")
        self.assertTrue(payload["secret_status"]["telegram_bot_token_configured"])

    def test_snapshot_command_outputs_public_state_and_run_paths(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "snapshot",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["config"]["web"]["pin_hash"], "")
        self.assertIn("run_paths", payload)
        self.assertIn("direct_use_command", payload["run_paths"])
        self.assertIn("snapshot_command", payload["run_paths"])
        self.assertIn("pause_command", payload["run_paths"])

    def test_paths_command_outputs_direct_use_summary(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "paths",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(payload["status"], {"ok", "warn"})
        self.assertIn("run_paths", payload)
        self.assertIn("direct_use_command", payload["run_paths"])
        self.assertIn("paths_command", payload["run_paths"])
        self.assertIn("capabilities", payload)
        self.assertIn("activation_summary", payload)
        self.assertIn("remaining_gaps", payload)
        self.assertEqual(payload["wiring"]["okx_execution_path"], "simulated_demo")

    def test_direct_use_command_outputs_text_summary(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "direct-use",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("TG OKX Auto Trade Direct-Use Summary", result.stdout)
        self.assertIn("http://127.0.0.1:6010/login", result.stdout)
        self.assertIn("runtime_direct_use_json", result.stdout)
        self.assertIn("topic_delivery:", result.stdout)
        self.assertIn("profile_detail:", result.stdout)
        self.assertIn("next_action:", result.stdout)
        self.assertIn("demo-only", result.stdout)

    def test_direct_use_command_outputs_json_payload(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "direct-use",
                "--config",
                str(self.root / "config.json"),
                "--json",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["run_paths"]["web_login"], "http://127.0.0.1:6010/login")
        self.assertEqual(payload["activation_summary"]["manual_demo"]["status"], "ready")
        self.assertIn("remaining_gaps", payload)

    def test_live_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.runtime.update_config({"trading": {"mode": "live"}})

    def test_init_config_command_writes_hashed_pin(self):
        target = self.root / "fresh-config.json"
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "init-config",
                "--config",
                str(target),
                "--pin",
                "654321",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["web"]["pin_hash"], hash_pin("654321"))

    def test_externalize_secrets_command_moves_inline_credentials_to_local_env(self):
        repo_root = Path(__file__).resolve().parents[1]
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["telegram"]["bot_token"] = "bot-secret"
        config_payload["okx"]["enabled"] = True
        config_payload["okx"]["api_key"] = "api-key"
        config_payload["okx"]["api_secret"] = "api-secret"
        config_payload["okx"]["passphrase"] = "passphrase"
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "externalize-secrets",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["secret_sources"]["telegram_bot_token"], "env")
        self.assertEqual(payload["secret_sources"]["okx_demo_credentials"], "env")

        persisted = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["telegram"]["bot_token"], "")
        self.assertEqual(persisted["okx"]["api_key"], "")
        self.assertEqual(persisted["okx"]["api_secret"], "")
        self.assertEqual(persisted["okx"]["passphrase"], "")

        env_text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("TG_OKX_TELEGRAM_BOT_TOKEN=", env_text)
        self.assertIn("TG_OKX_OKX_API_KEY=", env_text)
        self.assertIn("TG_OKX_OKX_API_SECRET=", env_text)
        self.assertIn("TG_OKX_OKX_PASSPHRASE=", env_text)

        reloaded = Runtime(self.root / "config.json")
        self.addCleanup(reloaded.stop)
        snapshot = reloaded.public_snapshot()
        self.assertTrue(snapshot["secret_status"]["telegram_bot_token_configured"])
        self.assertTrue(snapshot["secret_status"]["okx_demo_credentials_configured"])
        self.assertEqual(snapshot["secret_sources"]["telegram_bot_token"], "env")
        self.assertEqual(snapshot["secret_sources"]["okx_demo_credentials"], "env")

    def test_observe_mode_records_without_executing(self):
        self.runtime.update_config({"trading": {"mode": "observe"}})
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "OBSERVED")
        self.assertEqual(snapshot["orders"][0]["status"], "observed")
        self.assertEqual(snapshot["dashboard"]["positions_count"], 0)

    def test_execution_failure_auto_pauses_trading(self):
        with mock.patch.object(self.runtime.okx, "execute", side_effect=RuntimeError("bad credentials")):
            self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.snapshot()
        self.assertTrue(snapshot["config"]["trading"]["paused"])
        self.assertEqual(snapshot["messages"][0]["status"], "EXECUTION_FAILED")
        self.assertEqual(snapshot["health"]["okx_rest"]["status"], "error")
        self.assertEqual(self.runtime.capability_summary()["okx_demo_execution"]["status"], "error")
        self.assertIn("trading_paused", {item["id"] for item in self.runtime.remaining_gaps()})

    def test_okx_environment_mismatch_is_explained_in_operator_surfaces(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "live-key",
                    "api_secret": "demo-secret",
                    "passphrase": "demo-passphrase",
                }
            }
        )
        with mock.patch.object(
            self.runtime.okx,
            "_request",
            return_value={"code": "50101", "msg": "APIKey does not match current environment."},
        ):
            self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.snapshot()
        hint = "live environment or another wrong environment key"
        self.assertEqual(snapshot["messages"][0]["status"], "EXECUTION_FAILED")
        self.assertIn("APIKey does not match current environment.", snapshot["health"]["okx_rest"]["detail"])
        self.assertIn(hint, snapshot["health"]["okx_rest"]["detail"])
        checks = {item["name"]: item for item in self.runtime.readiness_checks()}
        self.assertIn(hint, checks["okx_demo"]["detail"])
        capability = self.runtime.capability_summary()["okx_demo_execution"]
        self.assertEqual(capability["status"], "error")
        self.assertIn(hint, capability["detail"])

    def test_manual_close_positions_closes_simulated_position(self):
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        result = self.runtime.close_positions("BTC-USDT-SWAP")
        self.assertEqual(result["closed"][0]["status"], "filled")
        snapshot = self.runtime.snapshot()
        positions = snapshot["positions"]
        self.assertEqual(positions[0]["payload"]["side"], "flat")
        self.assertEqual(positions[0]["payload"]["qty"], 0.0)
        self.assertEqual(snapshot["dashboard"]["positions_count"], 0)
        self.assertEqual(snapshot["dashboard"]["tracked_symbols_count"], 1)

    def test_manual_close_returns_already_flat_when_polled_exchange_position_is_zero(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        self.runtime.okx.restore_simulated_state(
            [
                {
                    "symbol": "BTC-USDT-SWAP",
                    "payload": {
                        "symbol": "BTC-USDT-SWAP",
                        "qty": 1.0,
                        "side": "long",
                        "avg_price": 100.0,
                        "margin_mode": "isolated",
                        "leverage": 20,
                        "realized_pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "protection": {},
                        "exchange_protection_orders": [],
                        "source": "local_expected",
                        "updated_at": "2026-03-18T00:00:00+00:00",
                    },
                }
            ]
        )

        def fake_request(method, path, body=None):
            self.assertEqual(method, "GET")
            self.assertIn("/api/v5/account/positions?instId=BTC-USDT-SWAP", path)
            return {
                "code": "0",
                "msg": "",
                "data": [{"instId": "BTC-USDT-SWAP", "pos": "0", "posSide": "net", "mgnMode": "isolated", "lever": "20", "avgPx": "", "upl": "0"}],
            }

        with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
            result = self.runtime.close_positions("BTC-USDT-SWAP")
        snapshot = self.runtime.snapshot()
        self.assertEqual(result["closed"][0]["status"], "already_flat")
        self.assertEqual(snapshot["positions"][0]["payload"]["qty"], 0.0)
        self.assertEqual(snapshot["positions"][0]["payload"]["side"], "flat")
        self.assertEqual(snapshot["positions"][0]["payload"]["source"], "exchange_polled")

    def test_manual_close_stays_simulated_for_simulated_positions_even_when_okx_demo_is_enabled(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        with mock.patch.object(self.runtime.okx, "_request", side_effect=AssertionError("real OKX demo path should not be used")):
            self.runtime.inject_message("LONG BTCUSDT SIZE 1", "-1001", 204)
            result = self.runtime.close_positions("BTC-USDT-SWAP")
        self.assertEqual(result["closed"][0]["status"], "filled")
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["orders"][0]["payload"]["execution_path"], "simulated_demo")
        self.assertEqual(snapshot["positions"][0]["payload"]["side"], "flat")
        self.assertEqual(snapshot["positions"][0]["payload"]["qty"], 0.0)

    def test_reset_local_runtime_state_clears_local_history_and_positions(self):
        self.runtime.process_message(self._message("LONG BTCUSDT SIZE 1"))
        self.runtime.authenticate("123456")
        self.assertTrue(self.runtime.snapshot()["orders"])

        result = self.runtime.reset_local_runtime_state()

        self.assertEqual(result["status"], "ok")
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["messages"], [])
        self.assertEqual(snapshot["orders"], [])
        self.assertEqual(snapshot["positions"], [])
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertEqual(snapshot["events"][0]["message"], "Local runtime state reset")
        self.assertEqual(snapshot["dashboard"]["positions_count"], 0)
        self.assertEqual(snapshot["dashboard"]["tracked_symbols_count"], 0)
        self.assertEqual(len(snapshot["audit_logs"]), 1)
        self.assertEqual(snapshot["audit_logs"][0]["message"], "Local runtime state reset")
        self.assertEqual(len(snapshot["config"]["telegram"]["channels"]), 1)
        self.assertEqual(self.runtime.okx.positions(), [])
        self.assertIn("does not cancel exchange orders", result["detail"])

    def test_real_demo_path_sets_leverage_before_order_and_caches_it(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        requests: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if path == "/api/v5/account/set-leverage":
                return {"code": "0", "msg": "", "data": [{"lever": body["lever"], "mgnMode": body["mgnMode"]}]}
            if path == "/api/v5/trade/order":
                return {"code": "0", "msg": "", "data": [{"ordId": "12345", "sCode": "0", "sMsg": ""}]}
            raise AssertionError(f"unexpected OKX path: {path}")

        with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
            self.runtime.process_message(self._message("LONG BTCUSDT SIZE 1"))
            self.runtime.process_message(self._message("ADD BTCUSDT SIZE 1", version=2))

        self.assertEqual([item[1] for item in requests], [
            "/api/v5/account/set-leverage",
            "/api/v5/trade/order",
            "/api/v5/trade/order",
        ])
        self.assertEqual(requests[0][2]["lever"], "20")
        self.assertEqual(requests[0][2]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(requests[1][2]["clOrdId"][:2], "tg")
        self.assertEqual(self.runtime.snapshot()["orders"][0]["payload"]["data"][0]["ordId"], "12345")

    def test_real_demo_order_includes_attached_algo_orders_for_absolute_tp_sl(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        requests: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if path == "/api/v5/account/set-leverage":
                return {"code": "0", "msg": "", "data": [{"lever": body["lever"], "mgnMode": body["mgnMode"]}]}
            if path == "/api/v5/trade/order":
                return {"code": "0", "msg": "", "data": [{"ordId": "12345", "sCode": "0", "sMsg": ""}]}
            raise AssertionError(f"unexpected OKX path: {path}")

        with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
            self.runtime.process_message(self._message("LONG BTCUSDT SIZE 1 TP 105000 SL 99000"))

        order_body = requests[1][2]
        self.assertEqual(len(order_body["attachAlgoOrds"]), 2)
        self.assertEqual(order_body["attachAlgoOrds"][0]["tpTriggerPx"], "105000.0")
        self.assertEqual(order_body["attachAlgoOrds"][0]["tpOrdPx"], "-1")
        self.assertEqual(order_body["attachAlgoOrds"][1]["slTriggerPx"], "99000.0")
        self.assertEqual(order_body["attachAlgoOrds"][1]["slOrdPx"], "-1")
        snapshot = self.runtime.snapshot()
        self.assertEqual(len(snapshot["orders"][0]["payload"]["attached_algo_orders"]), 2)
        self.assertEqual(len(snapshot["positions"][0]["payload"]["exchange_protection_orders"]), 2)

    def test_real_demo_http_error_includes_status_and_response_body(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        http_error = urllib.error.HTTPError(
            url="https://www.okx.com/api/v5/trade/order",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b"error code: 1010"),
        )
        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(RuntimeError) as ctx:
                self.runtime.okx._request("POST", "/api/v5/trade/order", {"instId": "BTC-USDT-SWAP"})
        message = str(ctx.exception)
        self.assertIn("HTTP 403 Forbidden", message)
        self.assertIn("response body: error code: 1010", message)
        self.assertIn("POST /api/v5/trade/order", message)

    def test_real_demo_request_sets_user_agent_header(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )

        def fake_urlopen(req, data=None, timeout=30):
            self.assertEqual(req.headers.get("User-agent"), "tg-okx-auto-trade/1.0")
            self.assertEqual(req.headers.get("X-simulated-trading"), "1")
            self.assertIsNotNone(data)

            class _Response:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

                def read(self_inner):
                    return b'{"code":"0","msg":"","data":[]}'

            return _Response()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            payload = self.runtime.okx._request("POST", "/api/v5/account/set-leverage", {"instId": "BTC-USDT-SWAP"})
        self.assertEqual(payload["code"], "0")

    def test_real_demo_get_request_does_not_send_body(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )

        def fake_urlopen(req, data=None, timeout=30):
            self.assertEqual(req.headers.get("User-agent"), "tg-okx-auto-trade/1.0")
            self.assertEqual(req.headers.get("X-simulated-trading"), "1")
            self.assertIsNone(data)

            class _Response:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

                def read(self_inner):
                    return b'{"code":"0","msg":"","data":[]}'

            return _Response()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            payload = self.runtime.okx._request("GET", "/api/v5/account/positions?instId=BTC-USDT-SWAP")
        self.assertEqual(payload["code"], "0")

    def test_manual_inject_defaults_to_simulated_even_when_okx_demo_is_configured(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        with mock.patch.object(self.runtime.okx, "_request", side_effect=AssertionError("real OKX demo path should not be used")):
            self.runtime.inject_message("LONG BTCUSDT SIZE 1", "-1001", 201)
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "EXECUTED")
        self.assertEqual(snapshot["orders"][0]["payload"]["execution_path"], "simulated_demo")

    def test_manual_inject_can_opt_into_real_okx_demo_path(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        requests: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if path == "/api/v5/account/set-leverage":
                return {"code": "0", "msg": "", "data": [{"lever": body["lever"], "mgnMode": body["mgnMode"]}]}
            if path == "/api/v5/trade/order":
                return {"code": "0", "msg": "", "data": [{"ordId": "12345", "sCode": "0", "sMsg": ""}]}
            raise AssertionError(f"unexpected OKX path: {path}")

        with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
            self.runtime.inject_message("LONG BTCUSDT SIZE 1", "-1001", 202, use_configured_okx_path=True)
        snapshot = self.runtime.snapshot()
        self.assertEqual([item[1] for item in requests], ["/api/v5/account/set-leverage", "/api/v5/trade/order"])
        self.assertEqual(snapshot["orders"][0]["payload"]["execution_path"], "real_demo_rest")

    def test_manual_inject_cancel_orders_can_use_real_okx_demo_path(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        requests: list[tuple[str, str, object | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if path == "/api/v5/account/set-leverage":
                return {"code": "0", "msg": "", "data": [{"lever": body["lever"], "mgnMode": body["mgnMode"]}]}
            if path == "/api/v5/trade/order":
                return {"code": "0", "msg": "", "data": [{"ordId": "12345", "sCode": "0", "sMsg": ""}]}
            if path == "/api/v5/trade/cancel-algos":
                return {
                    "code": "0",
                    "msg": "",
                    "data": [
                        {"algoClOrdId": item["algoClOrdId"], "sCode": "0", "sMsg": ""}
                        for item in body
                    ],
                }
            raise AssertionError(f"unexpected OKX path: {path}")

        with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
            self.runtime.inject_message("LONG BTCUSDT SIZE 1 TP 105000 SL 99000", "-1001", 202, use_configured_okx_path=True)
            self.runtime.inject_message("CANCEL BTCUSDT", "-1001", 203, use_configured_okx_path=True)
        snapshot = self.runtime.snapshot()
        self.assertEqual(
            [item[1] for item in requests],
            ["/api/v5/account/set-leverage", "/api/v5/trade/order", "/api/v5/trade/cancel-algos"],
        )
        cancel_body = requests[2][2]
        self.assertEqual(len(cancel_body), 2)
        self.assertEqual(cancel_body[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(cancel_body[0]["ordType"], "conditional")
        self.assertTrue(cancel_body[0]["algoClOrdId"].startswith("tg"))
        self.assertEqual(snapshot["orders"][0]["payload"]["execution_path"], "real_demo_rest")
        self.assertEqual(snapshot["orders"][0]["status"], "canceled")
        self.assertEqual(snapshot["orders"][0]["payload"]["cancel_mode"], "okx_demo_rest")
        self.assertEqual(snapshot["positions"][0]["payload"]["protection"], {})
        self.assertEqual(snapshot["positions"][0]["payload"]["exchange_protection_orders"], [])

    def test_real_demo_reverse_path_closes_then_reopens_to_target_side(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        self.runtime.okx.restore_simulated_state(
            [
                {
                    "symbol": "BTC-USDT-SWAP",
                    "payload": {
                        "symbol": "BTC-USDT-SWAP",
                        "qty": 2.0,
                        "side": "short",
                        "avg_price": 100.0,
                        "margin_mode": "isolated",
                        "leverage": 20,
                        "realized_pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "protection": {},
                        "updated_at": "2026-03-17T00:00:00+00:00",
                    },
                }
            ]
        )
        requests: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if path == "/api/v5/account/set-leverage":
                return {"code": "0", "msg": "", "data": [{"lever": body["lever"], "mgnMode": body["mgnMode"]}]}
            if path == "/api/v5/trade/order":
                return {
                    "code": "0",
                    "msg": "",
                    "data": [{"ordId": f"ord-{len(requests)}", "sCode": "0", "sMsg": ""}],
                }
            raise AssertionError(f"unexpected OKX path: {path}")

        with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
            self.runtime.inject_message(
                "REVERSE LONG BTCUSDT SIZE 3",
                "-1001",
                203,
                use_configured_okx_path=True,
            )

        snapshot = self.runtime.snapshot()
        self.assertEqual(
            [item[1] for item in requests],
            ["/api/v5/account/set-leverage", "/api/v5/trade/order", "/api/v5/trade/order"],
        )
        self.assertEqual(requests[1][2]["reduceOnly"], "true")
        self.assertEqual(requests[1][2]["side"], "buy")
        self.assertEqual(requests[1][2]["sz"], "2.0")
        self.assertEqual(requests[2][2]["side"], "buy")
        self.assertNotIn("reduceOnly", requests[2][2])
        self.assertEqual(requests[2][2]["sz"], "3.0")
        self.assertEqual(snapshot["orders"][0]["payload"]["execution_path"], "real_demo_rest")
        self.assertEqual(len(snapshot["orders"][0]["payload"]["steps"]), 2)
        self.assertEqual(snapshot["positions"][0]["payload"]["side"], "long")

    def test_public_web_openclaw_close_all_syncs_exchange_position_before_real_demo_close(self):
        self.runtime.update_config(
            {
                "ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"},
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                },
            }
        )
        requests: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if method == "GET" and path == "/api/v5/account/positions?instId=BTC-USDT-SWAP":
                return {
                    "code": "0",
                    "msg": "",
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "pos": "3",
                            "posSide": "net",
                            "mgnMode": "isolated",
                            "lever": "20",
                            "avgPx": "100",
                            "upl": "0",
                        }
                    ],
                }
            if method == "POST" and path == "/api/v5/trade/order":
                return {"code": "0", "msg": "", "data": [{"ordId": "close-12345", "sCode": "0", "sMsg": ""}]}
            raise AssertionError(f"unexpected OKX request: {(method, path, body)}")

        openclaw_reply = json.dumps(
            {
                "executable": True,
                "action": "close",
                "symbol": "BTC-USDT-SWAP",
                "market_type": "swap",
                "side": None,
                "entry_type": "market",
                "size_mode": None,
                "size_value": None,
                "leverage": 20,
                "margin_mode": "isolated",
                "risk_level": "medium",
                "tp": [],
                "sl": None,
                "trailing": None,
                "require_manual_confirmation": False,
                "confidence": 0.98,
                "reason": "close",
            }
        )
        message = NormalizedMessage.from_public_web(
            "cryptoninjas_trading_ann",
            "new",
            {
                "channel_username": "cryptoninjas_trading_ann",
                "message_id": 99202,
                "date": "2026-03-20T01:49:46+00:00",
                "text": "CLOSE BTCUSDT NOW",
                "caption": "",
            },
        )

        with mock.patch.object(self.runtime.ai, "_run_openclaw", return_value=openclaw_reply):
            with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
                self.runtime.process_message(message)

        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "EXECUTED")
        self.assertEqual([item[1] for item in requests], ["/api/v5/account/positions?instId=BTC-USDT-SWAP", "/api/v5/trade/order"])
        self.assertEqual(requests[1][2]["side"], "sell")
        self.assertEqual(requests[1][2]["sz"], "3.0")
        self.assertEqual(requests[1][2]["reduceOnly"], "true")
        self.assertEqual(snapshot["orders"][0]["action"], "close_all")
        self.assertEqual(snapshot["orders"][0]["payload"]["execution_path"], "real_demo_rest")
        self.assertEqual(snapshot["positions"][0]["payload"]["side"], "flat")
        self.assertEqual(snapshot["positions"][0]["payload"]["qty"], 0.0)

    def test_public_web_openclaw_close_all_skips_unknown_okx_instrument_without_auto_pause(self):
        self.runtime.update_config(
            {
                "ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"},
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                },
            }
        )
        requests: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if method == "GET" and path == "/api/v5/account/positions?instId=MOODENG-USDT-SWAP":
                return {
                    "code": "51000",
                    "msg": "Instrument ID, Instrument ID code, or Spread ID doesn't exist.",
                    "data": [],
                }
            raise AssertionError(f"unexpected OKX request: {(method, path, body)}")

        openclaw_reply = json.dumps(
            {
                "executable": True,
                "action": "close",
                "symbol": "MOODENG-USDT-SWAP",
                "market_type": "swap",
                "side": None,
                "entry_type": "market",
                "size_mode": None,
                "size_value": None,
                "leverage": 20,
                "margin_mode": "isolated",
                "risk_level": "medium",
                "tp": [],
                "sl": None,
                "trailing": None,
                "require_manual_confirmation": False,
                "confidence": 0.98,
                "reason": "close at entry",
            }
        )
        message = NormalizedMessage.from_public_web(
            "cryptoninjas_trading_ann",
            "new",
            {
                "channel_username": "cryptoninjas_trading_ann",
                "message_id": 99203,
                "date": "2026-03-20T01:49:46+00:00",
                "text": "MOODENG close at entry, wait for new entry",
                "caption": "",
            },
        )

        with mock.patch.object(self.runtime.ai, "_run_openclaw", return_value=openclaw_reply):
            with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
                self.runtime.process_message(message)

        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "EXECUTED")
        self.assertFalse(snapshot["config"]["trading"]["paused"])
        self.assertEqual([item[1] for item in requests], ["/api/v5/account/positions?instId=MOODENG-USDT-SWAP"])
        self.assertEqual(snapshot["orders"][0]["action"], "close_all")
        self.assertEqual(snapshot["orders"][0]["status"], "skipped")
        self.assertEqual(snapshot["orders"][0]["payload"]["execution_path"], "real_demo_rest")
        self.assertIn("does not recognize the instrument id", snapshot["orders"][0]["payload"]["reason"])

    def test_public_web_openclaw_close_all_skips_when_exchange_position_is_already_flat(self):
        self.runtime.update_config(
            {
                "ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"},
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                },
            }
        )
        requests: list[tuple[str, str, dict[str, object] | None]] = []

        def fake_request(method, path, body=None):
            requests.append((method, path, body))
            if method == "GET" and path == "/api/v5/account/positions?instId=BTC-USDT-SWAP":
                return {
                    "code": "0",
                    "msg": "",
                    "data": [
                        {
                            "instId": "BTC-USDT-SWAP",
                            "pos": "0",
                            "posSide": "net",
                            "mgnMode": "isolated",
                            "lever": "20",
                            "avgPx": "",
                            "upl": "0",
                        }
                    ],
                }
            raise AssertionError(f"unexpected OKX request: {(method, path, body)}")

        openclaw_reply = json.dumps(
            {
                "executable": True,
                "action": "close",
                "symbol": "BTC-USDT-SWAP",
                "market_type": "swap",
                "side": None,
                "entry_type": "market",
                "size_mode": None,
                "size_value": None,
                "leverage": 20,
                "margin_mode": "isolated",
                "risk_level": "medium",
                "tp": [],
                "sl": None,
                "trailing": None,
                "require_manual_confirmation": False,
                "confidence": 0.98,
                "reason": "close flat",
            }
        )
        message = NormalizedMessage.from_public_web(
            "cryptoninjas_trading_ann",
            "new",
            {
                "channel_username": "cryptoninjas_trading_ann",
                "message_id": 99204,
                "date": "2026-03-20T01:49:46+00:00",
                "text": "BTC close at entry, wait for new entry",
                "caption": "",
            },
        )

        with mock.patch.object(self.runtime.ai, "_run_openclaw", return_value=openclaw_reply):
            with mock.patch.object(self.runtime.okx, "_request", side_effect=fake_request):
                self.runtime.process_message(message)

        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "EXECUTED")
        self.assertFalse(snapshot["config"]["trading"]["paused"])
        self.assertEqual([item[1] for item in requests], ["/api/v5/account/positions?instId=BTC-USDT-SWAP"])
        self.assertEqual(snapshot["orders"][0]["status"], "skipped")
        self.assertIn("no open quantity", snapshot["orders"][0]["payload"]["reason"])

    def test_invalid_position_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.runtime.update_config({"trading": {"position_mode": "long_short"}})

    def test_invalid_semi_automatic_execution_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.runtime.update_config({"trading": {"execution_mode": "semi_automatic"}})

    def test_config_validation_accepts_public_web_channel_without_chat_id(self):
        updated = self.runtime.update_config(
            {
                "telegram": {
                    "channels": [
                        self._public_web_channel(channel_username="https://t.me/s/lbeobhpreo"),
                    ]
                }
            }
        )
        channel = updated.telegram.channels[0]
        self.assertEqual(channel.source_type, "public_web")
        self.assertEqual(channel.channel_username, "lbeobhpreo")
        self.assertEqual(channel.chat_id, "")

    def test_readiness_warns_for_mtproto_channels(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "channels": [
                        {
                            "id": "mtproto-test",
                            "name": "MTProto Test",
                            "source_type": "mtproto",
                            "chat_id": "-1009",
                            "channel_username": "",
                            "enabled": True,
                            "priority": 100,
                            "parse_profile_id": "default",
                            "strategy_profile_id": "default",
                            "risk_profile_id": "default",
                            "paper_trading_enabled": True,
                            "live_trading_enabled": False,
                            "listen_new_messages": True,
                            "listen_edits": True,
                            "listen_deletes": False,
                            "reconcile_interval_seconds": 30,
                            "dedup_window_seconds": 3600,
                            "notes": ""
                        }
                    ]
                }
            }
        )
        checks = {item["name"]: item["status"] for item in self.runtime.readiness_checks()}
        self.assertEqual(checks["telegram_mtproto"], "warn")

    def test_readiness_and_remaining_gaps_warn_when_delete_events_are_requested(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "bot_token": "demo-bot-token",
                    "channels": [
                        {
                            "id": "vip-btc",
                            "name": "VIP BTC",
                            "source_type": "bot_api",
                            "chat_id": "-1001",
                            "channel_username": "",
                            "enabled": True,
                            "priority": 100,
                            "parse_profile_id": "default",
                            "strategy_profile_id": "default",
                            "risk_profile_id": "default",
                            "paper_trading_enabled": True,
                            "live_trading_enabled": False,
                            "listen_new_messages": True,
                            "listen_edits": True,
                            "listen_deletes": True,
                            "reconcile_interval_seconds": 30,
                            "dedup_window_seconds": 3600,
                            "notes": "",
                        }
                    ],
                }
            }
        )
        checks = {item["name"]: item for item in self.runtime.readiness_checks()}
        gaps = {item["id"]: item for item in self.runtime.remaining_gaps()}
        self.assertEqual(checks["telegram_delete_events"]["status"], "warn")
        self.assertIn("vip-btc", checks["telegram_delete_events"]["detail"])
        self.assertEqual(gaps["telegram_delete_events"]["status"], "partial")
        self.assertIn("vip-btc", gaps["telegram_delete_events"]["detail"])

    def test_public_web_html_parses_into_normalized_messages_and_detects_edits(self):
        self.runtime.update_config({"telegram": {"channels": [self._public_web_channel()]}})
        channel = self.runtime.config_manager.get().telegram.channels[0]
        watcher = self.runtime.telegram
        initial_html = """
        <div class="tgme_widget_message" data-post="lbeobhpreo/101">
          <div class="tgme_widget_message_text">LONG BTCUSDT<br>Entry now</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:34:56+00:00"></time></div>
        </div>
        """
        edited_html = """
        <div class="tgme_widget_message" data-post="lbeobhpreo/101">
          <div class="tgme_widget_message_text">LONG BTCUSDT<br>Entry updated</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:34:56+00:00"></time></div>
        </div>
        """

        parsed_posts = parse_public_channel_html("lbeobhpreo", initial_html)
        self.assertEqual(len(parsed_posts), 1)
        self.assertEqual(parsed_posts[0]["message_id"], 101)
        self.assertEqual(parsed_posts[0]["text"], "LONG BTCUSDT\nEntry now")

        normalized_new = watcher._normalize_public_web_post(channel, parsed_posts[0])
        self.assertIsNotNone(normalized_new)
        self.assertEqual(normalized_new.adapter, "public_web")
        self.assertEqual(normalized_new.chat_id, "public:lbeobhpreo")
        self.assertEqual(normalized_new.event_type, "new")
        self.assertEqual(normalized_new.version, 1)
        self.assertEqual(normalized_new.text, "LONG BTCUSDT\nEntry now")

        normalized_edit = watcher._normalize_public_web_post(
            channel,
            parse_public_channel_html("lbeobhpreo", edited_html)[0],
        )
        self.assertIsNotNone(normalized_edit)
        self.assertEqual(normalized_edit.event_type, "edit")
        self.assertEqual(normalized_edit.version, 2)
        self.assertEqual(normalized_edit.text, "LONG BTCUSDT\nEntry updated")
        self.assertIsNotNone(normalized_edit.edit_date)

    def test_public_web_first_poll_bootstraps_visible_history_without_replay(self):
        self.runtime.update_config({"telegram": {"channels": [self._public_web_channel()]}})
        channel = self.runtime.config_manager.get().telegram.channels[0]
        watcher = self.runtime.telegram
        first_html = """
        <div class="tgme_widget_message" data-post="lbeobhpreo/101">
          <div class="tgme_widget_message_text">LONG BTCUSDT</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:34:56+00:00"></time></div>
        </div>
        <div class="tgme_widget_message" data-post="lbeobhpreo/102">
          <div class="tgme_widget_message_text">SHORT ETHUSDT</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:35:56+00:00"></time></div>
        </div>
        """
        later_html = """
        <div class="tgme_widget_message" data-post="lbeobhpreo/101">
          <div class="tgme_widget_message_text">LONG BTCUSDT</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:34:56+00:00"></time></div>
        </div>
        <div class="tgme_widget_message" data-post="lbeobhpreo/102">
          <div class="tgme_widget_message_text">SHORT ETHUSDT</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:35:56+00:00"></time></div>
        </div>
        <div class="tgme_widget_message" data-post="lbeobhpreo/103">
          <div class="tgme_widget_message_text">LONG SOLUSDT</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:36:56+00:00"></time></div>
        </div>
        """
        callback = mock.Mock()

        with mock.patch.object(watcher, "_get_public_channel_html", return_value=first_html):
            emitted = watcher._poll_public_web_channels([channel], callback)
        self.assertEqual(emitted, 0)
        callback.assert_not_called()

        with mock.patch.object(watcher, "_get_public_channel_html", return_value=later_html):
            emitted = watcher._poll_public_web_channels([channel], callback)
        self.assertEqual(emitted, 1)
        callback.assert_called_once()
        emitted_message = callback.call_args[0][0]
        self.assertEqual(emitted_message.message_id, 103)
        self.assertEqual(emitted_message.event_type, "new")

    def test_public_web_restart_reuses_persisted_baseline_without_replaying_visible_posts(self):
        self.runtime.update_config({"telegram": {"channels": [self._public_web_channel()]}})
        channel = self.runtime.config_manager.get().telegram.channels[0]
        html = """
        <div class="tgme_widget_message" data-post="lbeobhpreo/101">
          <div class="tgme_widget_message_text">LONG BTCUSDT</div>
          <div class="tgme_widget_message_date"><time datetime="2026-03-18T12:34:56+00:00"></time></div>
        </div>
        """
        with mock.patch.object(self.runtime.telegram, "_get_public_channel_html", return_value=html):
            emitted = self.runtime.telegram._poll_public_web_channels([channel], mock.Mock())
        self.assertEqual(emitted, 0)

        fresh_runtime = Runtime(self.root / "config.json")
        self.addCleanup(fresh_runtime.stop)
        fresh_channel = fresh_runtime.config_manager.get().telegram.channels[0]
        callback = mock.Mock()
        with mock.patch.object(fresh_runtime.telegram, "_get_public_channel_html", return_value=html):
            emitted = fresh_runtime.telegram._poll_public_web_channels([fresh_channel], callback)
        self.assertEqual(emitted, 0)
        callback.assert_not_called()

    def test_management_message_status_is_not_risk_rejected(self):
        self.runtime.process_message(self._message("BTCUSDT 止盈：74500"))
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "MANAGEMENT_SKIPPED")
        self.assertEqual(snapshot["orders"], [])

    def test_runtime_topic_risk_broadcast_uses_chinese_template(self):
        self.runtime.update_config({"trading": {"readonly_close_only": True}})
        with mock.patch.object(self.runtime, "_send_topic_update") as send_topic:
            self.runtime.process_message(self._message("LONG BTCUSDT"))
        send_topic.assert_called_once()
        sent = send_topic.call_args.args[0]
        self.assertIn("[风控]", sent)
        self.assertIn("只允许平仓", sent)

    def test_runtime_topic_observe_broadcast_uses_chinese_template(self):
        self.runtime.update_config({"trading": {"mode": "observe"}})
        with mock.patch.object(self.runtime, "_send_topic_update") as send_topic:
            self.runtime.process_message(self._message("LONG BTCUSDT"))
        send_topic.assert_called_once()
        sent = send_topic.call_args.args[0]
        self.assertIn("[观察]", sent)
        self.assertIn("开多 BTC-USDT-SWAP", sent)
        self.assertIn("未实际下单", sent)

    def test_runtime_topic_trade_broadcast_uses_chinese_template(self):
        with mock.patch.object(self.runtime, "_send_topic_update") as send_topic:
            self.runtime.process_message(self._message("LONG BTCUSDT"))
        send_topic.assert_called_once()
        sent = send_topic.call_args.args[0]
        self.assertIn("[交易]", sent)
        self.assertIn("演示模式", sent)
        self.assertIn("开多 BTC-USDT-SWAP", sent)
        self.assertIn("已成交", sent)

    def test_runtime_topic_execution_error_broadcast_uses_chinese_template(self):
        with mock.patch.object(self.runtime.okx, "execute", side_effect=RuntimeError("bad credentials")):
            with mock.patch.object(self.runtime, "_send_topic_update") as send_topic:
                self.runtime.process_message(self._message("LONG BTCUSDT"))
        self.assertGreaterEqual(send_topic.call_count, 1)
        sent = send_topic.call_args_list[-1].args[0]
        self.assertIn("[执行失败]", sent)
        self.assertIn("开多 BTC-USDT-SWAP", sent)
        self.assertIn("bad credentials", sent)

    def test_telegram_watcher_reports_connected_health_for_public_web_without_bot_token(self):
        self.runtime.update_config({"telegram": {"bot_token": "", "channels": [self._public_web_channel()]}})
        health_events = []
        callback = mock.Mock()

        def fake_poll(channels, message_callback):
            self.assertEqual(channels[0].source_type, "public_web")
            self.runtime.telegram.stop_event.set()
            return 0

        self.runtime.telegram.health_callback = lambda status, detail: health_events.append((status, detail))
        with mock.patch.object(self.runtime.telegram, "_poll_public_web_channels", side_effect=fake_poll):
            self.runtime.telegram.run_forever(callback)

        self.assertTrue(health_events)
        self.assertEqual(health_events[-1][0], "connected")
        self.assertIn("public_web channel", health_events[-1][1])

    def test_global_tp_sl_enabled_applies_protection_to_new_position(self):
        self.runtime.update_config(
            {
                "trading": {
                    "global_tp_sl_enabled": True,
                    "global_take_profit_ratio": 55.0,
                    "global_stop_loss_ratio": 18.0,
                }
            }
        )
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.snapshot()
        protection = snapshot["positions"][0]["payload"]["protection"]
        self.assertEqual(protection["tp"][0]["ratio"], 55.0)
        self.assertEqual(protection["sl"]["ratio"], 18.0)
        self.assertTrue(snapshot["orders"][0]["payload"]["attached_algo_orders"])

    def test_reverse_signal_switches_demo_position_side(self):
        self.runtime.process_message(self._message("LONG BTCUSDT size 40"))
        self.runtime.process_message(self._message("REVERSE SHORT BTCUSDT size 25", version=2))
        positions = self.runtime.snapshot()["positions"]
        self.assertEqual(positions[0]["payload"]["side"], "short")
        self.assertEqual(positions[0]["payload"]["qty"], 25.0)

    def test_cancel_orders_clears_position_protection(self):
        self.runtime.update_config(
            {
                "trading": {
                    "global_tp_sl_enabled": True,
                    "global_take_profit_ratio": 40.0,
                    "global_stop_loss_ratio": 12.0,
                }
            }
        )
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        self.runtime.process_message(self._message("CANCEL BTCUSDT", version=2))
        positions = self.runtime.snapshot()["positions"]
        self.assertEqual(positions[0]["payload"]["side"], "long")
        self.assertEqual(positions[0]["payload"]["protection"], {})
        self.assertEqual(self.runtime.snapshot()["orders"][0]["status"], "canceled")

    def test_resume_trading_clears_pause_state(self):
        with mock.patch.object(self.runtime.okx, "execute", side_effect=RuntimeError("bad credentials")):
            self.runtime.process_message(self._message("LONG BTCUSDT"))
        self.runtime.resume_trading("Credentials fixed")
        snapshot = self.runtime.snapshot()
        self.assertFalse(snapshot["config"]["trading"]["paused"])
        self.assertEqual(snapshot["health"]["trading_runtime"]["status"], "running")
        self.assertEqual(snapshot["operator_state"]["last_resume_reason"], "Credentials fixed")

    def test_channel_upsert_normalizes_username_and_persists(self):
        channel = self.runtime.upsert_channel(
            {
                "name": "VIP BTC",
                "source_type": "bot_api",
                "channel_username": "https://t.me/Vip_BTC",
                "enabled": True,
            }
        )
        self.assertEqual(channel["id"], "vip_btc")
        payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["telegram"]["channels"][-1]["channel_username"], "Vip_BTC")
        self.runtime.set_channel_enabled("vip_btc", False)
        updated = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertFalse(updated["telegram"]["channels"][-1]["enabled"])
        self.runtime.remove_channel("vip_btc")
        removed = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertFalse(any(item["id"] == "vip_btc" for item in removed["telegram"]["channels"]))

    def test_channel_upsert_normalizes_chat_id_link(self):
        channel = self.runtime.upsert_channel(
            {
                "name": "Linked Channel",
                "source_type": "bot_api",
                "chat_id": "https://t.me/c/3720752566/2080",
                "enabled": True,
            }
        )
        self.assertEqual(channel["chat_id"], "-1003720752566")
        self.assertEqual(channel["id"], "chan-3720752566")

    def test_channel_upsert_public_web_persists_username_and_ignores_chat_id(self):
        channel = self.runtime.upsert_channel(
            {
                "name": "VIP Public",
                "source_type": "public_web",
                "chat_id": "https://t.me/c/3720752566/2080",
                "channel_username": "https://t.me/s/lbeobhpreo",
                "enabled": True,
            }
        )
        self.assertEqual(channel["id"], "lbeobhpreo")
        self.assertEqual(channel["channel_username"], "lbeobhpreo")
        self.assertEqual(channel["chat_id"], "")
        payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        saved = next(item for item in payload["telegram"]["channels"] if item["id"] == "lbeobhpreo")
        self.assertEqual(saved["source_type"], "public_web")
        self.assertEqual(saved["channel_username"], "lbeobhpreo")
        self.assertEqual(saved["chat_id"], "")

    def test_operator_topic_link_is_normalized_in_runtime_wiring(self):
        updated = self.runtime.update_config(
            {
                "telegram": {
                    "operator_target": "https://t.me/c/3720752566/2080",
                }
            }
        )
        self.assertEqual(updated.telegram.operator_target, "-1003720752566:topic:2080")
        self.assertEqual(updated.telegram.operator_thread_id, 2080)
        wiring = self.runtime.wiring_summary()
        self.assertEqual(wiring["topic_target"], "-1003720752566:topic:2080")
        self.assertEqual(wiring["topic_target_source"], "operator_target")
        self.assertEqual(wiring["topic_target_link"], "https://t.me/c/3720752566/2080")
        self.assertEqual(wiring["topic_chat_id"], "-1003720752566")
        self.assertEqual(wiring["topic_thread_id"], 2080)
        self.assertEqual(wiring["operator_command_ingress"], "configured_without_bot_token")
        self.assertEqual(self.runtime.snapshot()["config"]["telegram"]["operator_thread_id"], 2080)

    def test_report_topic_link_is_used_when_operator_target_missing(self):
        updated = self.runtime.update_config(
            {
                "telegram": {
                    "report_topic": "https://t.me/c/3720752566/2080",
                    "operator_target": "",
                }
            }
        )
        self.assertEqual(updated.telegram.report_topic, "-1003720752566:topic:2080")
        self.assertEqual(updated.telegram.operator_thread_id, 2080)
        wiring = self.runtime.wiring_summary()
        self.assertEqual(wiring["topic_target"], "-1003720752566:topic:2080")
        self.assertEqual(wiring["topic_target_source"], "report_topic")
        self.assertEqual(wiring["topic_thread_id"], 2080)

    def test_usage_paths_include_runtime_locations_and_examples(self):
        paths = self.runtime.usage_paths()
        repo_root = Path(__file__).resolve().parents[1]
        self.assertEqual(Path(paths["repo_root"]).resolve(), repo_root)
        self.assertEqual(paths["repo_root_hint"], f"cd {repo_root}")
        self.assertTrue(paths["runtime_state_dir"].endswith("/data"))
        self.assertTrue(paths["sqlite_path"].endswith("/data/app.db"))
        self.assertTrue(paths["runtime_direct_use_json"].endswith("/data/direct-use.json"))
        self.assertTrue(paths["runtime_direct_use_text"].endswith("/data/direct-use.txt"))
        self.assertTrue(paths["runtime_public_state_json"].endswith("/data/public-state.json"))
        self.assertIn("curl -i", paths["curl_login_command"])
        self.assertIn("/healthz", paths["curl_healthz_command"])
        self.assertIn("/readyz", paths["curl_readyz_command"])
        self.assertEqual(paths["manual_signal_default_path"], "simulated_demo")
        self.assertEqual(paths["manual_signal_configured_path"], "simulated_demo")
        self.assertEqual(paths["topic_target_input_example"], "https://t.me/c/3720752566/2080")
        self.assertEqual(paths["source_channel_message_link_example"], "https://t.me/c/1234567890/42")
        self.assertIn("-1001234567890", paths["channel_input_examples"])
        self.assertIn("https://t.me/channel_name", paths["channel_input_examples"])
        self.assertIn("https://t.me/c/1234567890/42", paths["channel_input_examples"])
        self.assertEqual(paths["topic_target_link"], "")
        self.assertIn("verify_demo.py", paths["verify_demo_command"])
        self.assertIn("direct-use", paths["direct_use_command"])
        self.assertIn("smoke_config.py", paths["smoke_config_command"])
        self.assertIn("smoke_cli.py", paths["smoke_cli_command"])
        self.assertIn("smoke_runtime.py", paths["smoke_runtime_command"])
        self.assertIn("smoke_e2e.py", paths["smoke_e2e_command"])
        self.assertIn("smoke_web.py", paths["smoke_web_command"])
        self.assertIn("smoke_operator.py", paths["smoke_operator_command"])
        self.assertIn("smoke_telegram.py", paths["smoke_telegram_command"])
        self.assertIn("smoke_http_server.py", paths["smoke_http_server_command"])
        self.assertIn("run_demo_suite.py", paths["smoke_suite_command"])
        self.assertIn("set-topic-target", paths["set_topic_target_command"])
        self.assertIn("upsert-channel", paths["upsert_channel_command"])
        self.assertIn("set-channel-enabled", paths["disable_channel_command"])
        self.assertIn("remove-channel", paths["remove_channel_command"])
        self.assertTrue(paths["local_env_path"].endswith(".env"))
        self.assertTrue(paths["project_env_path"].endswith(".env"))
        self.assertTrue(paths["env_example_path"].endswith(".env.example"))
        self.assertIn("externalize-secrets", paths["externalize_secrets_command"])
        self.assertIn("/readiness", paths["operator_command_examples"])
        self.assertIn("/topic-test", paths["operator_command_examples"])
        self.assertGreaterEqual(len(paths["activation_checklist"]), 3)
        self.assertEqual(paths["setup_examples"]["operator_target"], "https://t.me/c/3720752566/2080")
        self.assertEqual(paths["setup_examples"]["source_channel"]["source_type"], "public_web")
        self.assertEqual(
            paths["setup_examples"]["source_channel"]["channel_username"],
            "https://t.me/s/lbeobhpreo",
        )
        self.assertNotIn("bot_token", paths["setup_examples"]["telegram_patch"]["telegram"])
        self.assertEqual(paths["setup_examples"]["okx_demo_patch"]["okx"]["api_key"], "<set-locally>")
        self.assertEqual(
            paths["setup_examples"]["telegram_patch"]["telegram"]["channels"][0]["id"],
            "vip-public",
        )

    def test_runtime_writes_public_runtime_artifacts(self):
        self.runtime.update_config({"telegram": {"operator_target": "https://t.me/c/3720752566/2080"}})
        self.runtime.register_web_server("127.0.0.1", 6010)
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        artifact_paths = self.runtime.runtime_artifact_paths()
        direct_use = json.loads(Path(artifact_paths["runtime_direct_use_json"]).read_text(encoding="utf-8"))
        direct_use_text = Path(artifact_paths["runtime_direct_use_text"]).read_text(encoding="utf-8")
        public_state = json.loads(Path(artifact_paths["runtime_public_state_json"]).read_text(encoding="utf-8"))
        self.assertEqual(direct_use["run_paths"]["runtime_direct_use_json"], artifact_paths["runtime_direct_use_json"])
        self.assertEqual(direct_use["run_paths"]["runtime_direct_use_text"], artifact_paths["runtime_direct_use_text"])
        self.assertEqual(direct_use["wiring"]["topic_thread_id"], 2080)
        self.assertEqual(direct_use["run_paths"]["web_login"], "http://127.0.0.1:6010/login")
        self.assertTrue(direct_use["next_steps"])
        self.assertIn("TG OKX Auto Trade Direct-Use Summary", direct_use_text)
        self.assertIn("http://127.0.0.1:6010/login", direct_use_text)
        self.assertIn("inject_demo", direct_use_text)
        self.assertIn("profile_detail:", direct_use_text)
        self.assertIn("next_action:", direct_use_text)
        self.assertIn("demo-only", direct_use_text)
        self.assertEqual(public_state["config"]["telegram"]["operator_thread_id"], 2080)
        self.assertEqual(public_state["config"]["web"]["pin_hash"], "")
        self.assertEqual(public_state["orders"][0]["status"], "filled")
        self.assertIn(public_state["verification_status"], {"ok", "warn"})
        self.assertTrue(public_state["next_steps"])

    def test_unregister_web_server_clears_active_bind_state(self):
        self.runtime.register_web_server("127.0.0.1", 6010)
        self.assertTrue(self.runtime.wiring_summary()["web_server_active"])
        self.runtime.unregister_web_server()
        wiring = self.runtime.wiring_summary()
        self.assertFalse(wiring["web_server_active"])
        self.assertEqual(wiring["web_bind"], "127.0.0.1:6010")

    def test_usage_paths_include_topic_and_channel_links(self):
        self.runtime.update_config({"telegram": {"operator_target": "https://t.me/c/3720752566/2080"}})
        self.runtime.upsert_channel(
            {
                "name": "VIP BTC",
                "source_type": "bot_api",
                "channel_username": "https://t.me/Vip_BTC",
                "enabled": True,
            }
        )
        paths = self.runtime.usage_paths()
        self.assertEqual(paths["topic_target_link"], "https://t.me/c/3720752566/2080")
        self.assertEqual(paths["topic_chat_id"], "-1003720752566")
        self.assertEqual(paths["topic_thread_id"], 2080)
        self.assertEqual(paths["operator_command_ingress"], "configured_without_bot_token")
        self.assertEqual(paths["manual_signal_default_path"], "simulated_demo")
        self.assertIn("https://t.me/Vip_BTC", paths["enabled_channel_links"])
        self.assertEqual(paths["setup_examples"]["operator_target"], "https://t.me/c/3720752566/2080")

    def test_usage_paths_disable_helper_prefers_existing_disabled_channel_id(self):
        self.runtime.update_config({"telegram": {"channels": []}})
        self.runtime.upsert_channel(
            {
                "id": "disabled-only",
                "name": "Disabled Only",
                "source_type": "bot_api",
                "chat_id": "-1002",
                "enabled": False,
            }
        )
        paths = self.runtime.usage_paths()
        self.assertIn("--channel-id disabled-only --disabled", paths["disable_channel_command"])
        self.assertIn("--channel-id disabled-only", paths["remove_channel_command"])
        self.assertEqual(paths["channel_helper_target"], "disabled-only")

    def test_usage_paths_channel_helpers_use_placeholder_without_channels(self):
        self.runtime.update_config({"telegram": {"channels": []}})
        paths = self.runtime.usage_paths()
        self.assertIn("<channel-id-from-upsert-channel>", paths["disable_channel_command"])
        self.assertIn("<channel-id-from-upsert-channel>", paths["remove_channel_command"])
        self.assertNotIn("vip_btc", paths["disable_channel_command"])
        self.assertEqual(paths["channel_helper_target"], "")

    def test_remove_channel_prunes_channel_storage_records(self):
        self.runtime.upsert_channel(
            {
                "id": "extra",
                "name": "Extra",
                "source_type": "bot_api",
                "chat_id": "-1009",
                "enabled": True,
            }
        )
        self.runtime.remove_channel("extra")
        with sqlite3.connect(self.runtime.storage.path) as conn:
            rows = conn.execute("SELECT id FROM channels ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in rows], ["test"])

    def test_capability_summary_exposes_demo_local_readiness_and_blockers(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "channels": [],
                    "operator_target": "https://t.me/c/3720752566/2080",
                },
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                },
            }
        )
        with mock.patch.object(
            self.runtime,
            "_endpoint_reachability",
            return_value={"status": "reachable", "detail": "www.okx.com:443 resolved successfully"},
        ):
            capabilities = self.runtime.capability_summary()
            activation = self.runtime.activation_summary()
            paths = self.runtime.usage_paths()
        self.assertEqual(capabilities["current_operating_profile"]["status"], "manual_ready")
        self.assertIn("direct manual/demo use", capabilities["current_operating_profile"]["detail"])
        self.assertIn("enabled public_web source channel", capabilities["current_operating_profile"]["detail"])
        self.assertEqual(capabilities["manual_demo_pipeline"]["status"], "ready")
        self.assertEqual(capabilities["okx_demo_execution"]["status"], "ready")
        self.assertEqual(capabilities["telegram_ingestion"]["status"], "blocked")
        self.assertIn("public_web", capabilities["telegram_ingestion"]["detail"])
        self.assertEqual(capabilities["operator_topic"]["status"], "partial")
        self.assertIn("not been verified yet", capabilities["operator_topic"]["detail"])
        self.assertEqual(activation["overall_profile"]["status"], "manual_ready")
        self.assertEqual(activation["manual_demo"]["status"], "ready")
        self.assertEqual(activation["automatic_telegram"]["status"], "blocked")
        self.assertEqual(activation["operator_topic_outbound"]["status"], "configured")
        self.assertEqual(activation["operator_topic_inbound"]["status"], "legacy")
        self.assertEqual(capabilities["demo_only_guard"]["status"], "locked")
        self.assertEqual(paths["manual_signal_default_path"], "simulated_demo")
        self.assertEqual(paths["manual_signal_configured_path"], "real_demo_rest")
        self.assertIn("cancel_orders", paths["configured_okx_supported_actions"])
        self.assertIn("reverse_to_long", paths["configured_okx_supported_actions"])
        self.assertIn("close_all", paths["configured_okx_supported_actions"])
        self.assertNotIn("cancel_orders", paths["configured_okx_unsupported_actions"])
        self.assertNotIn("reverse_to_long", paths["configured_okx_unsupported_actions"])

    def test_public_web_readiness_allows_automatic_ingestion_without_bot_token(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "bot_token": "",
                    "channels": [self._public_web_channel()],
                    "operator_target": "https://t.me/c/3720752566/2080",
                }
            }
        )
        capabilities = self.runtime.capability_summary()
        activation = self.runtime.activation_summary()
        gaps = {item["id"]: item for item in self.runtime.remaining_gaps()}
        checks = {item["name"]: item for item in self.runtime.readiness_checks()}
        wiring = self.runtime.wiring_summary()

        self.assertEqual(capabilities["telegram_ingestion"]["status"], "ready")
        self.assertIn("intended supported automatic ingestion path", capabilities["telegram_ingestion"]["detail"])
        self.assertEqual(activation["automatic_telegram"]["status"], "ready")
        self.assertEqual(checks["telegram_watcher"]["status"], "pass")
        self.assertIn("public_web", checks["telegram_watcher"]["detail"])
        self.assertNotIn("telegram_bot_token", gaps)
        self.assertNotIn("telegram_operator_inbound_token", gaps)
        self.assertEqual(activation["operator_topic_inbound"]["status"], "legacy")
        self.assertEqual(wiring["operator_command_ingress"], "configured_without_bot_token")

    def test_heuristic_parser_extracts_symbol_from_public_web_hashtag_signal(self):
        ai = OpenClawAI(self.runtime.config_manager.get())
        message = NormalizedMessage.from_public_web(
            "lbeobhpreo",
            "new",
            {
                "channel_username": "lbeobhpreo",
                "message_id": 12001,
                "date": "2026-03-18T00:00:00+00:00",
                "text": "#FARTCOIN——市价多",
                "caption": "",
            },
        )
        intent = ai.parse(message, [], {})
        self.assertTrue(intent.executable)
        self.assertEqual(intent.action, "open_long")
        self.assertEqual(intent.symbol, "FARTCOIN-USDT-SWAP")

    def test_heuristic_parser_ignores_take_profit_broadcast_without_fresh_entry(self):
        ai = OpenClawAI(self.runtime.config_manager.get())
        message = NormalizedMessage.from_public_web(
            "lbeobhpreo",
            "new",
            {
                "channel_username": "lbeobhpreo",
                "message_id": 12002,
                "date": "2026-03-18T00:00:00+00:00",
                "text": "#ANIME TP3止盈已觸發，簡簡單單跌幅15%",
                "caption": "",
            },
        )
        intent = ai.parse(message, [], {})
        self.assertFalse(intent.executable)
        self.assertEqual(intent.action, "ignore")

    def test_heuristic_parser_ignores_ambiguous_chatter_without_symbol(self):
        ai = OpenClawAI(self.runtime.config_manager.get())
        message = NormalizedMessage.from_public_web(
            "lbeobhpreo",
            "new",
            {
                "channel_username": "lbeobhpreo",
                "message_id": 12003,
                "date": "2026-03-18T00:00:00+00:00",
                "text": "看看能不能多空双吃！",
                "caption": "",
            },
        )
        intent = ai.parse(message, [], {})
        self.assertFalse(intent.executable)
        self.assertEqual(intent.action, "ignore")
        self.assertEqual(intent.symbol, "")

    def test_ai_prompt_uses_structured_channel_context(self):
        ai = OpenClawAI(self.runtime.config_manager.get())
        message = NormalizedMessage.from_public_web(
            "lbeobhpreo",
            "new",
            {
                "channel_username": "lbeobhpreo",
                "message_id": 12010,
                "date": "2026-03-18T00:00:00+00:00",
                "text": "BTCUSDT 止盈：74500",
                "caption": "",
            },
        )
        prompt = ai._build_prompt(
            message,
            [
                {
                    "chat_id": "public:lbeobhpreo",
                    "message_id": 12001,
                    "event_type": "new",
                    "version": 1,
                    "text": "LONG BTCUSDT",
                    "caption": "",
                },
                {
                    "chat_id": "public:lbeobhpreo",
                    "message_id": 12002,
                    "event_type": "new",
                    "version": 1,
                    "text": "止损 69000",
                    "caption": "",
                },
            ],
            {"mode": "demo", "positions": [{"payload": {"symbol": "BTC-USDT-SWAP", "side": "long", "size": "1"}}]},
        )
        self.assertIn("Structured channel context:", prompt)
        self.assertIn('"same_symbol_messages"', prompt)
        self.assertIn('"recent_management_messages"', prompt)
        self.assertIn('"role_hint": "management_update"', prompt)
        self.assertNotIn("Recent channel context:", prompt)

    def test_non_heuristic_provider_records_fallback_metadata_when_ai_call_fails(self):
        updated = self.runtime.update_config({"ai": {"provider": "openclaw"}})
        ai = OpenClawAI(updated)
        message = NormalizedMessage.from_public_web(
            "lbeobhpreo",
            "new",
            {
                "channel_username": "lbeobhpreo",
                "message_id": 12004,
                "date": "2026-03-18T00:00:00+00:00",
                "text": "#FARTCOIN——市价多",
                "caption": "",
            },
        )
        with mock.patch.object(ai, "_run_openclaw", side_effect=RuntimeError("token invalid")):
            intent = ai.parse(message, [], {})
        self.assertEqual(intent.symbol, "FARTCOIN-USDT-SWAP")
        self.assertEqual(intent.raw["parser_source"], "heuristic_fallback")
        self.assertEqual(intent.raw["requested_provider"], "openclaw")
        self.assertIn("token invalid", intent.raw["provider_error"])

    def test_runtime_health_exposes_ai_fallback_reason(self):
        self.runtime.update_config({"ai": {"provider": "openclaw"}})
        message = NormalizedMessage.from_public_web(
            "lbeobhpreo",
            "new",
            {
                "channel_username": "lbeobhpreo",
                "message_id": 12005,
                "date": "2026-03-18T00:00:00+00:00",
                "text": "#FARTCOIN——市价多",
                "caption": "",
            },
        )
        with mock.patch.object(self.runtime.ai, "_run_openclaw", side_effect=RuntimeError("token invalid")):
            self.runtime.process_message(message)
        snapshot = self.runtime.snapshot()
        health = snapshot["health"]["openclaw_agent"]
        decision = snapshot["ai_decisions"][0]["payload"]
        self.assertEqual(health["status"], "heuristic_fallback")
        self.assertIn("token invalid", health["detail"])
        self.assertEqual(decision["raw"]["parser_source"], "heuristic_fallback")
        self.assertIn("token invalid", decision["raw"]["provider_error"])

    def test_run_openclaw_extracts_text_from_wrapped_json_payloads(self):
        updated = self.runtime.update_config({"ai": {"openclaw_agent_id": "tgokxai"}})
        ai = OpenClawAI(updated)
        wrapped = {
            "runId": "123",
            "status": "ok",
            "result": {
                "payloads": [
                    {"text": '{"executable": true, "action": "open_long", "symbol": "FARTCOIN-USDT-SWAP", "market_type": "swap", "side": "buy", "entry_type": "market", "size_mode": "fixed_usdt", "size_value": 100.0, "leverage": 20, "margin_mode": "isolated", "risk_level": "medium", "tp": [], "sl": null, "trailing": null, "require_manual_confirmation": false, "confidence": 0.9, "reason": "ok"}'}
                ]
            }
        }
        completed = subprocess.CompletedProcess(args=["openclaw"], returncode=0, stdout=json.dumps(wrapped), stderr="")
        with mock.patch("subprocess.run", return_value=completed) as mocked:
            raw = ai._run_openclaw("prompt")
        self.assertIn('FARTCOIN-USDT-SWAP', raw)
        called = mocked.call_args.args[0]
        self.assertIn("tgokxai", called)
        self.assertNotIn("--local", called)

    def test_provider_error_includes_raw_auth_failure_text(self):
        updated = self.runtime.update_config({"ai": {"provider": "openclaw"}})
        ai = OpenClawAI(updated)
        message = NormalizedMessage.from_public_web(
            "lbeobhpreo",
            "new",
            {
                "channel_username": "lbeobhpreo",
                "message_id": 12006,
                "date": "2026-03-18T00:00:00+00:00",
                "text": "#FARTCOIN——市价多",
                "caption": "",
            },
        )
        with mock.patch.object(ai, "_run_openclaw", return_value="Your authentication token has been invalidated. Please try signing in again."):
            intent = ai.parse(message, [], {})
        self.assertEqual(intent.raw["parser_source"], "heuristic_fallback")
        self.assertIn("authentication token has been invalidated", intent.raw["provider_error"])

    def test_openclaw_payload_defaults_missing_fields_and_normalizes_symbol(self):
        updated = self.runtime.update_config({"ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"}})
        ai = OpenClawAI(updated)
        wrapped = {
            "runId": "123",
            "status": "ok",
            "result": {
                "payloads": [
                    {"text": '{"executable": false, "action": "open_long", "symbol": "FARTCOIN", "market_type": null, "side": "buy", "entry_type": "market", "size_mode": null, "size_value": null, "leverage": null, "margin_mode": null, "risk_level": "medium", "tp": [], "sl": null, "trailing": null, "require_manual_confirmation": true, "confidence": 0.96, "reason": "need more detail"}'}
                ]
            }
        }
        completed = subprocess.CompletedProcess(args=["openclaw"], returncode=0, stdout=json.dumps(wrapped), stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            message = NormalizedMessage.from_public_web(
                "lbeobhpreo",
                "new",
                {
                    "channel_username": "lbeobhpreo",
                    "message_id": 12007,
                    "date": "2026-03-18T00:00:00+00:00",
                    "text": "#FARTCOIN——市价多",
                    "caption": "",
                },
            )
            intent = ai.parse(message, [], {})
        self.assertFalse(intent.executable)
        self.assertTrue(intent.require_manual_confirmation)
        self.assertEqual(intent.symbol, "FARTCOIN-USDT-SWAP")
        self.assertEqual(intent.market_type, "swap")
        self.assertEqual(intent.size_mode, "fixed_usdt")
        self.assertEqual(intent.size_value, 100.0)
        self.assertEqual(intent.leverage, 20)
        self.assertEqual(intent.margin_mode, "isolated")
        self.assertEqual(intent.raw["parser_source"], "openclaw")

    def test_openclaw_ignore_clears_ambiguous_short_symbol_and_defaults_risk_level(self):
        updated = self.runtime.update_config({"ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"}})
        ai = OpenClawAI(updated)
        wrapped = {
            "runId": "124",
            "status": "ok",
            "result": {
                "payloads": [
                    {"text": '{"executable": false, "action": "ignore", "symbol": "M", "market_type": null, "side": null, "entry_type": null, "size_mode": null, "size_value": null, "leverage": null, "margin_mode": null, "risk_level": null, "tp": [], "sl": null, "trailing": null, "require_manual_confirmation": true, "confidence": 0.98, "reason": "ignore"}'}
                ]
            }
        }
        completed = subprocess.CompletedProcess(args=["openclaw"], returncode=0, stdout=json.dumps(wrapped), stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            message = NormalizedMessage.from_public_web(
                "lbeobhpreo",
                "new",
                {
                    "channel_username": "lbeobhpreo",
                    "message_id": 12008,
                    "date": "2026-03-18T00:00:00+00:00",
                    "text": "#M ，tp2止盈拿下👌",
                    "caption": "",
                },
            )
            intent = ai.parse(message, [], {})
        self.assertFalse(intent.executable)
        self.assertEqual(intent.action, "ignore")
        self.assertEqual(intent.symbol, "")
        self.assertEqual(intent.risk_level, "medium")
        self.assertEqual(intent.side, "flat")
        self.assertFalse(intent.require_manual_confirmation)

    def test_openclaw_ignore_clears_manual_confirmation_for_broadcast_text(self):
        updated = self.runtime.update_config({"ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"}})
        ai = OpenClawAI(updated)
        wrapped = {
            "runId": "125",
            "status": "ok",
            "result": {
                "payloads": [
                    {"text": '{"executable": false, "action": "ignore", "symbol": null, "market_type": null, "side": null, "entry_type": null, "size_mode": null, "size_value": null, "leverage": null, "margin_mode": null, "risk_level": null, "tp": [], "sl": null, "trailing": null, "require_manual_confirmation": true, "confidence": 0.97, "reason": "Broadcast/update only"}'}
                ]
            }
        }
        completed = subprocess.CompletedProcess(args=["openclaw"], returncode=0, stdout=json.dumps(wrapped), stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            message = NormalizedMessage.from_public_web(
                "lbeobhpreo",
                "new",
                {
                    "channel_username": "lbeobhpreo",
                    "message_id": 12009,
                    "date": "2026-03-18T00:00:00+00:00",
                    "text": "会员群继续拿下！",
                    "caption": "",
                },
            )
            intent = ai.parse(message, [], {})
        self.assertEqual(intent.action, "ignore")
        self.assertFalse(intent.require_manual_confirmation)
        self.assertEqual(intent.symbol, "")
        self.assertEqual(intent.side, "flat")

    def test_openclaw_aliases_close_to_close_all(self):
        updated = self.runtime.update_config({"ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"}})
        ai = OpenClawAI(updated)
        wrapped = {
            "runId": "126",
            "status": "ok",
            "result": {"payloads": [{"text": '{"executable": true, "action": "close", "symbol": "MOODENG", "market_type": "swap", "side": null, "entry_type": null, "size_mode": null, "size_value": null, "leverage": null, "margin_mode": null, "risk_level": "medium", "tp": [], "sl": null, "trailing": null, "require_manual_confirmation": false, "confidence": 0.94, "reason": "close it"}'}]}
        }
        completed = subprocess.CompletedProcess(args=["openclaw"], returncode=0, stdout=json.dumps(wrapped), stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            message = NormalizedMessage.from_public_web("cryptoninjas_trading_ann", "new", {"channel_username": "cryptoninjas_trading_ann", "message_id": 8778, "date": "2026-03-18T00:00:00+00:00", "text": "CHR close at entry, wait for new entry", "caption": ""})
            intent = ai.parse(message, [], {})
        self.assertEqual(intent.action, "close_all")
        self.assertEqual(intent.symbol, "MOODENG-USDT-SWAP")
        self.assertEqual(intent.side, "flat")

    def test_openclaw_aliases_cancel_entry_to_cancel_orders(self):
        updated = self.runtime.update_config({"ai": {"provider": "openclaw", "openclaw_agent_id": "tgokxai"}})
        ai = OpenClawAI(updated)
        wrapped = {
            "runId": "127",
            "status": "ok",
            "result": {"payloads": [{"text": '{"executable": true, "action": "cancel_entry", "symbol": "TOWNS", "market_type": "swap", "side": null, "entry_type": null, "size_mode": null, "size_value": null, "leverage": null, "margin_mode": null, "risk_level": "medium", "tp": [], "sl": null, "trailing": null, "require_manual_confirmation": false, "confidence": 0.94, "reason": "cancel limit"}'}]}
        }
        completed = subprocess.CompletedProcess(args=["openclaw"], returncode=0, stdout=json.dumps(wrapped), stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            message = NormalizedMessage.from_public_web("cryptoninjas_trading_ann", "new", {"channel_username": "cryptoninjas_trading_ann", "message_id": 8777, "date": "2026-03-18T00:00:00+00:00", "text": "TOWNS cancel entry limit", "caption": ""})
            intent = ai.parse(message, [], {})
        self.assertEqual(intent.action, "cancel_orders")
        self.assertEqual(intent.symbol, "TOWNS-USDT-SWAP")
        self.assertEqual(intent.side, "flat")

    def test_capability_summary_warns_when_okx_demo_endpoint_is_unreachable(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        with mock.patch.object(
            self.runtime,
            "_endpoint_reachability",
            return_value={"status": "unreachable", "detail": "www.okx.com:443 resolution failed: boom"},
        ):
            capabilities = self.runtime.capability_summary()
            gaps = {item["id"]: item for item in self.runtime.remaining_gaps()}
            checks = {item["name"]: item for item in self.runtime.readiness_checks()}
        self.assertEqual(capabilities["okx_demo_execution"]["status"], "partial")
        self.assertIn("resolution failed", capabilities["okx_demo_execution"]["detail"])
        self.assertEqual(gaps["okx_rest_connectivity"]["status"], "attention")
        self.assertIn("resolution failed", gaps["okx_rest_connectivity"]["detail"])
        self.assertEqual(checks["okx_demo"]["status"], "warn")
        self.assertIn("resolution failed", checks["okx_demo"]["detail"])

    def test_capability_summary_marks_topic_delivery_disabled_when_env_requests_it(self):
        self.runtime.update_config({"telegram": {"operator_target": "https://t.me/c/3720752566/2080"}})
        with mock.patch.dict(os.environ, {"TG_OKX_DISABLE_TOPIC_SEND": "1"}):
            capabilities = self.runtime.capability_summary()
            gaps = {item["id"]: item for item in self.runtime.remaining_gaps()}
        self.assertEqual(capabilities["operator_topic"]["status"], "disabled")
        self.assertIn("TG_OKX_DISABLE_TOPIC_SEND=1", capabilities["operator_topic"]["detail"])
        self.assertEqual(gaps["operator_topic_outbound"]["status"], "disabled")
        self.assertIn("TG_OKX_DISABLE_TOPIC_SEND=1", gaps["operator_topic_outbound"]["detail"])

    def test_verification_report_exposes_capabilities(self):
        report = self.runtime.public_verification_report()
        self.assertIn("capabilities", report)
        self.assertIn("activation_summary", report)
        self.assertIn("remaining_gaps", report)
        self.assertIn("telegram_ingestion", report["capabilities"])
        self.assertIn("current_operating_profile", report["capabilities"])
        self.assertIn("operator_topic_outbound", report["activation_summary"])
        self.assertEqual(report["capabilities"]["demo_only_guard"]["status"], "locked")
        self.assertIn("telegram_source_legacy_bot_api", {item["id"] for item in report["remaining_gaps"]})

    def test_verification_report_preserves_placeholder_setup_examples(self):
        report = self.runtime.public_verification_report()
        setup_examples = report["run_paths"]["setup_examples"]
        self.assertNotIn("bot_token", setup_examples["telegram_patch"]["telegram"])
        self.assertEqual(setup_examples["source_channel"]["source_type"], "public_web")
        self.assertEqual(setup_examples["okx_demo_patch"]["okx"]["api_key"], "<set-locally>")
        self.assertEqual(setup_examples["okx_demo_patch"]["okx"]["api_secret"], "<set-locally>")
        self.assertEqual(setup_examples["okx_demo_patch"]["okx"]["passphrase"], "<set-locally>")

    def test_runtime_loads_bot_token_from_local_env_file_without_persisting_it(self):
        instance = self.root / "env-instance"
        instance.mkdir()
        config = json.loads(json.dumps(CONFIG_TEMPLATE))
        config["runtime"]["data_dir"] = "runtime-data"
        config["runtime"]["sqlite_path"] = "runtime-data/app.db"
        config["telegram"]["channels"] = [
            {
                "id": "vip-btc",
                "name": "VIP BTC",
                "source_type": "bot_api",
                "chat_id": "-1001",
                "channel_username": "",
                "enabled": True,
                "priority": 100,
                "parse_profile_id": "default",
                "strategy_profile_id": "default",
                "risk_profile_id": "default",
                "paper_trading_enabled": True,
                "live_trading_enabled": False,
                "listen_new_messages": True,
                "listen_edits": True,
                "listen_deletes": False,
                "reconcile_interval_seconds": 30,
                "dedup_window_seconds": 3600,
                "notes": "",
            }
        ]
        config["telegram"]["operator_target"] = "https://t.me/c/3720752566/2080"
        config_path = instance / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        (instance / ".env").write_text("TG_OKX_TELEGRAM_BOT_TOKEN=env-bot-token\n", encoding="utf-8")

        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            runtime = Runtime(config_path)
            self.addCleanup(runtime.stop)
            capabilities = runtime.capability_summary()
            wiring = runtime.wiring_summary()
            snapshot = runtime.public_snapshot()
            runtime.update_config({"trading": {"default_leverage": 21}})

        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(capabilities["telegram_ingestion"]["status"], "partial")
        self.assertEqual(capabilities["operator_topic"]["status"], "partial")
        self.assertEqual(snapshot["activation_summary"]["operator_topic_inbound"]["status"], "legacy")
        self.assertEqual(wiring["operator_command_ingress"], "ready")
        self.assertTrue(snapshot["secret_status"]["telegram_bot_token_configured"])
        self.assertEqual(snapshot["secret_sources"]["telegram_bot_token"], "env")
        self.assertEqual(snapshot["config"]["telegram"]["bot_token"], "")
        self.assertEqual(persisted["telegram"]["bot_token"], "")

    def test_runtime_loads_okx_demo_credentials_from_local_env_file_without_persisting_them(self):
        instance = self.root / "okx-env-instance"
        instance.mkdir()
        config = json.loads(json.dumps(CONFIG_TEMPLATE))
        config["runtime"]["data_dir"] = "runtime-data"
        config["runtime"]["sqlite_path"] = "runtime-data/app.db"
        config["okx"]["enabled"] = True
        config["okx"]["api_key"] = ""
        config["okx"]["api_secret"] = ""
        config["okx"]["passphrase"] = ""
        config_path = instance / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        (instance / ".env").write_text(
            "\n".join(
                [
                    "TG_OKX_OKX_API_KEY=env-okx-key",
                    "TG_OKX_OKX_API_SECRET=env-okx-secret",
                    "TG_OKX_OKX_PASSPHRASE=env-okx-passphrase",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            runtime = Runtime(config_path)
            self.addCleanup(runtime.stop)
            snapshot = runtime.public_snapshot()
            self.assertTrue(snapshot["secret_status"]["okx_demo_credentials_configured"])
            self.assertEqual(snapshot["secret_sources"]["okx_demo_credentials"], "env")
            self.assertEqual(snapshot["config"]["okx"]["api_key"], "")
            self.assertEqual(snapshot["config"]["okx"]["api_secret"], "")
            self.assertEqual(snapshot["config"]["okx"]["passphrase"], "")

            with mock.patch.object(runtime.okx, "_execute_real_demo") as execute_real_demo:
                execute_real_demo.return_value = mock.Mock(
                    status="filled",
                    exchange_order_id="demo-order-id",
                    payload={"execution_path": "real_demo_rest", "ordId": "demo-order-id"},
                    position_snapshot=None,
                )
                result = runtime.okx.execute(
                    TradingIntent(
                        executable=True,
                        action="open_long",
                        symbol="BTC-USDT-SWAP",
                        market_type="swap",
                        side="buy",
                        entry_type="market",
                        size_mode="contracts",
                        size_value=1.0,
                        leverage=20,
                        margin_mode="isolated",
                        risk_level="medium",
                        require_manual_confirmation=False,
                        confidence=0.9,
                        reason="env credential test",
                    )
                )

            runtime.update_config({"trading": {"default_leverage": 21}})

        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(result.payload["execution_path"], "real_demo_rest")
        self.assertEqual(persisted["okx"]["api_key"], "")
        self.assertEqual(persisted["okx"]["api_secret"], "")
        self.assertEqual(persisted["okx"]["passphrase"], "")

    def test_config_manager_reload_detects_local_env_bot_token_change(self):
        instance = self.root / "env-reload-instance"
        instance.mkdir()
        config = json.loads(json.dumps(CONFIG_TEMPLATE))
        config["runtime"]["data_dir"] = "runtime-data"
        config["runtime"]["sqlite_path"] = "runtime-data/app.db"
        config["telegram"]["channels"] = [
            {
                "id": "vip-btc",
                "name": "VIP BTC",
                "source_type": "bot_api",
                "chat_id": "-1001",
                "channel_username": "",
                "enabled": True,
                "priority": 100,
                "parse_profile_id": "default",
                "strategy_profile_id": "default",
                "risk_profile_id": "default",
                "paper_trading_enabled": True,
                "live_trading_enabled": False,
                "listen_new_messages": True,
                "listen_edits": True,
                "listen_deletes": False,
                "reconcile_interval_seconds": 30,
                "dedup_window_seconds": 3600,
                "notes": "",
            }
        ]
        config["telegram"]["operator_target"] = "https://t.me/c/3720752566/2080"
        config_path = instance / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            runtime = Runtime(config_path)
            self.addCleanup(runtime.stop)
            self.assertFalse(runtime.public_snapshot()["secret_status"]["telegram_bot_token_configured"])

            time.sleep(0.02)
            (instance / ".env").write_text("TG_OKX_TELEGRAM_BOT_TOKEN=reloaded-env-bot-token\n", encoding="utf-8")
            self.assertTrue(runtime.config_manager.reload_if_changed())
            runtime.on_config_change(runtime.config_manager.get())
            snapshot = runtime.public_snapshot()

        persisted = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(snapshot["secret_status"]["telegram_bot_token_configured"])
        self.assertEqual(snapshot["secret_sources"]["telegram_bot_token"], "env")
        self.assertEqual(snapshot["wiring"]["operator_command_ingress"], "ready")
        self.assertEqual(snapshot["capabilities"]["telegram_ingestion"]["status"], "partial")
        self.assertEqual(snapshot["activation_summary"]["operator_topic_inbound"]["status"], "legacy")
        self.assertEqual(snapshot["config"]["telegram"]["bot_token"], "")
        self.assertEqual(persisted["telegram"]["bot_token"], "")

    def test_config_manager_reload_detects_local_env_okx_credential_change(self):
        instance = self.root / "okx-env-reload-instance"
        instance.mkdir()
        config = json.loads(json.dumps(CONFIG_TEMPLATE))
        config["runtime"]["data_dir"] = "runtime-data"
        config["runtime"]["sqlite_path"] = "runtime-data/app.db"
        config["okx"]["api_key_env"] = "TEST_OKX_API_KEY"
        config["okx"]["api_secret_env"] = "TEST_OKX_API_SECRET"
        config["okx"]["passphrase_env"] = "TEST_OKX_PASSPHRASE"
        config_path = instance / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            runtime = Runtime(config_path)
            self.addCleanup(runtime.stop)
            self.assertFalse(runtime.public_snapshot()["secret_status"]["okx_demo_credentials_configured"])

            time.sleep(0.02)
            (instance / ".env").write_text(
                "\n".join(
                    [
                        "TEST_OKX_API_KEY=reloaded-okx-key",
                        "TEST_OKX_API_SECRET=reloaded-okx-secret",
                        "TEST_OKX_PASSPHRASE=reloaded-okx-passphrase",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(runtime.config_manager.reload_if_changed())
            runtime.on_config_change(runtime.config_manager.get())
            snapshot = runtime.public_snapshot()

        self.assertTrue(snapshot["secret_status"]["okx_demo_credentials_configured"])
        self.assertEqual(snapshot["secret_sources"]["okx_demo_credentials"], "env")
        self.assertEqual(snapshot["config"]["okx"]["api_key"], "")
        self.assertEqual(snapshot["config"]["okx"]["api_secret"], "")
        self.assertEqual(snapshot["config"]["okx"]["passphrase"], "")

    def test_capability_summary_marks_current_profile_ready_when_bot_and_channel_exist(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "bot_token": "demo-bot-token",
                    "channels": [
                        {
                            "id": "vip-btc",
                            "name": "VIP BTC",
                            "source_type": "bot_api",
                            "chat_id": "-1001",
                            "channel_username": "",
                            "enabled": True,
                            "priority": 100,
                            "parse_profile_id": "default",
                            "strategy_profile_id": "default",
                            "risk_profile_id": "default",
                            "paper_trading_enabled": True,
                            "live_trading_enabled": False,
                            "listen_new_messages": True,
                            "listen_edits": True,
                            "listen_deletes": False,
                            "reconcile_interval_seconds": 30,
                            "dedup_window_seconds": 3600,
                            "notes": "",
                        }
                    ],
                    "operator_target": "https://t.me/c/3720752566/2080",
                }
            }
        )
        capabilities = self.runtime.capability_summary()
        self.assertEqual(capabilities["current_operating_profile"]["status"], "ready")
        self.assertIn("demo-only execution", capabilities["current_operating_profile"]["detail"])

    def test_capability_summary_requires_operator_topic_for_ready_profile(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "bot_token": "demo-bot-token",
                    "channels": [
                        {
                            "id": "vip-btc",
                            "name": "VIP BTC",
                            "source_type": "bot_api",
                            "chat_id": "-1001",
                            "channel_username": "",
                            "enabled": True,
                            "priority": 100,
                            "parse_profile_id": "default",
                            "strategy_profile_id": "default",
                            "risk_profile_id": "default",
                            "paper_trading_enabled": True,
                            "live_trading_enabled": False,
                            "listen_new_messages": True,
                            "listen_edits": True,
                            "listen_deletes": False,
                            "reconcile_interval_seconds": 30,
                            "dedup_window_seconds": 3600,
                            "notes": "",
                        }
                    ],
                    "operator_target": "",
                    "report_topic": "",
                }
            }
        )
        capabilities = self.runtime.capability_summary()
        self.assertEqual(capabilities["current_operating_profile"]["status"], "manual_ready")
        self.assertIn("operator topic target", capabilities["current_operating_profile"]["detail"])

    def test_verification_report_marks_operator_topic_commands_as_legacy_scope(self):
        self.runtime.update_config({"telegram": {"operator_target": "https://t.me/c/3720752566/2080"}})
        report = self.runtime.public_verification_report()
        self.assertEqual(report["activation_summary"]["operator_topic_inbound"]["status"], "legacy")
        self.assertIn("legacy/internal", report["activation_summary"]["operator_topic_inbound"]["detail"])
        self.assertIn("legacy/internal", report["capabilities"]["operator_topic"]["detail"])

    def test_verification_report_next_steps_call_out_missing_public_web_channel(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "bot_token": "demo-bot-token",
                    "channels": [
                        {
                            "id": "mtproto-test",
                            "name": "MTProto Test",
                            "source_type": "mtproto",
                            "chat_id": "-1009",
                            "channel_username": "",
                            "enabled": True,
                            "priority": 100,
                            "parse_profile_id": "default",
                            "strategy_profile_id": "default",
                            "risk_profile_id": "default",
                            "paper_trading_enabled": True,
                            "live_trading_enabled": False,
                            "listen_new_messages": True,
                            "listen_edits": True,
                            "listen_deletes": False,
                            "reconcile_interval_seconds": 30,
                            "dedup_window_seconds": 3600,
                            "notes": "",
                        }
                    ],
                    "operator_target": "https://t.me/c/3720752566/2080",
                }
            }
        )
        report = self.runtime.public_verification_report()
        joined = "\n".join(report["next_steps"])
        self.assertIn("enabled `public_web` Telegram channel entry", joined)

    def test_verification_report_next_steps_include_topic_smoke_when_operator_topic_is_configured(self):
        self.runtime.update_config({"telegram": {"operator_target": "https://t.me/c/3720752566/2080"}})
        with mock.patch("tg_okx_auto_trade.runtime.shutil.which", return_value="/usr/bin/openclaw"):
            report = self.runtime.public_verification_report()
        joined = "\n".join(report["next_steps"])
        self.assertIn("topic-test", joined)
        self.assertIn("topic smoke", joined.lower())

    def test_verification_report_surfaces_partial_okx_action_coverage_gap(self):
        self.runtime.update_config(
            {
                "okx": {
                    "enabled": True,
                    "api_key": "api-key",
                    "api_secret": "api-secret",
                    "passphrase": "passphrase",
                }
            }
        )
        report = self.runtime.public_verification_report()
        gaps = {item["id"]: item for item in report["remaining_gaps"]}
        self.assertEqual(gaps["okx_demo_action_coverage"]["status"], "partial")
        self.assertNotIn("cancel_orders", gaps["okx_demo_action_coverage"]["detail"])
        self.assertIn("update_protection", gaps["okx_demo_action_coverage"]["detail"])
        self.assertIn("attached protection locally", gaps["okx_demo_action_coverage"]["detail"])
        self.assertNotIn("reverse_to_long", gaps["okx_demo_action_coverage"]["detail"])
        self.assertIn("reduce_long", report["wiring"]["configured_okx_supported_actions"])

    def test_topic_send_can_be_disabled_for_safe_smoke_runs(self):
        self.runtime.update_config({"telegram": {"operator_target": "-1003720752566:topic:2080"}})
        with mock.patch.dict(os.environ, {"TG_OKX_DISABLE_TOPIC_SEND": "1"}):
            runtime = Runtime(self.root / "config.json")
            self.addCleanup(runtime.stop)
            self.assertEqual(runtime.snapshot()["health"]["topic_logger"]["status"], "disabled")
            result = runtime.send_topic_test()
            self.assertFalse(result["sent"])
            self.assertIn("TG_OKX_DISABLE_TOPIC_SEND", result["reason"])
            self.assertEqual(result["target_link"], "https://t.me/c/3720752566/2080")
            self.assertEqual(runtime.snapshot()["health"]["topic_logger"]["status"], "disabled")

    def test_topic_failure_is_reflected_in_capability_summary(self):
        self.runtime.update_config({"telegram": {"operator_target": "-1003720752566:topic:2080"}})
        with mock.patch.object(
            self.runtime.topic_logger,
            "send",
            return_value={"sent": False, "status": "failed", "reason": "network blocked", "target": "-1003720752566:topic:2080"},
        ):
            result = self.runtime.send_topic_test()
        self.assertFalse(result["sent"])
        capability = self.runtime.capability_summary()["operator_topic"]
        gaps = {item["id"]: item for item in self.runtime.remaining_gaps()}
        next_steps = self.runtime.public_verification_report()["next_steps"]
        self.assertEqual(capability["status"], "error")
        self.assertIn("network blocked", capability["detail"])
        self.assertEqual(gaps["operator_topic_outbound"]["status"], "attention")
        self.assertIn("network blocked", gaps["operator_topic_outbound"]["detail"])
        self.assertTrue(any("Last operator-topic delivery failed" in item for item in next_steps))

    def test_wiring_summary_exposes_topic_delivery_state_and_verification(self):
        self.runtime.update_config({"telegram": {"operator_target": "-1003720752566:topic:2080"}})
        initial = self.runtime.wiring_summary()
        initial_activation = self.runtime.activation_summary()
        self.assertEqual(initial["topic_delivery_state"], "configured")
        self.assertFalse(initial["topic_delivery_verified"])
        self.assertIn("-1003720752566:topic:2080", initial["topic_delivery_detail"])
        self.assertEqual(initial_activation["operator_topic_outbound"]["status"], "configured")

        with mock.patch.object(
            self.runtime.topic_logger,
            "send",
            return_value={"sent": True, "status": "sent", "target": "-1003720752566:topic:2080"},
        ):
            result = self.runtime.send_topic_test()

        self.assertTrue(result["sent"])
        refreshed = self.runtime.wiring_summary()
        refreshed_activation = self.runtime.activation_summary()
        self.assertEqual(refreshed["topic_delivery_state"], "ok")
        self.assertTrue(refreshed["topic_delivery_verified"])
        self.assertIn("Topic smoke succeeded", refreshed["topic_delivery_detail"])
        self.assertEqual(refreshed_activation["operator_topic_outbound"]["status"], "ready")
        self.assertIn("verified in this runtime", refreshed_activation["operator_topic_outbound"]["detail"])

    def test_usage_paths_prefer_active_web_bind_and_flag_restart_requirement(self):
        self.runtime.register_web_server("127.0.0.1", 6010)
        before = self.runtime.usage_paths()
        self.assertEqual(before["web_login"], "http://127.0.0.1:6010/login")
        self.assertFalse(before["web_restart_required"])

        self.runtime.update_config({"web": {"port": 6011}})
        after = self.runtime.usage_paths()
        self.assertEqual(after["web_login"], "http://127.0.0.1:6010/login")
        self.assertEqual(after["configured_web_login"], "http://127.0.0.1:6011/login")
        self.assertTrue(after["web_restart_required"])
        checks = {item["name"]: item["detail"] for item in self.runtime.readiness_checks()}
        self.assertIn("restart serve", checks["web_bind"])

    def test_web_state_exposes_active_bind_and_restart_requirement(self):
        self.runtime.register_web_server("127.0.0.1", 6010)
        self.runtime.update_config({"web": {"port": 6011}})
        controller = WebController(self.runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]
        status, _, state = controller.route("GET", "/api/state", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertEqual(state["wiring"]["web_bind"], "127.0.0.1:6010")
        self.assertTrue(state["wiring"]["web_restart_required"])
        self.assertEqual(state["run_paths"]["configured_web_login"], "http://127.0.0.1:6011/login")

    def test_public_snapshot_redacts_secrets_and_exposes_wiring_summary(self):
        updated = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        updated["telegram"]["bot_token"] = "bot-secret"
        updated["telegram"]["operator_target"] = "-1003720752566:topic:2080"
        updated["okx"]["enabled"] = True
        updated["okx"]["api_key"] = "api-key"
        updated["okx"]["api_secret"] = "api-secret"
        updated["okx"]["passphrase"] = "passphrase"
        (self.root / "config.json").write_text(json.dumps(updated, indent=2), encoding="utf-8")
        self.runtime = Runtime(self.root / "config.json")
        self.runtime.update_config({"trading": {"default_leverage": 21}})
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.public_snapshot()
        self.assertEqual(snapshot["config"]["telegram"]["bot_token"], "")
        self.assertEqual(snapshot["config"]["okx"]["api_key"], "")
        self.assertEqual(snapshot["config"]["web"]["pin_hash"], "")
        self.assertTrue(snapshot["secret_status"]["telegram_bot_token_configured"])
        self.assertEqual(snapshot["wiring"]["topic_target"], "-1003720752566:topic:2080")
        self.assertEqual(snapshot["wiring"]["topic_target_link"], "https://t.me/c/3720752566/2080")
        self.assertEqual(snapshot["wiring"]["operator_command_ingress"], "ready")
        self.assertEqual(snapshot["wiring"]["okx_execution_path"], "real_demo_rest")
        self.assertEqual(snapshot["activation_summary"]["manual_demo"]["status"], "ready")
        self.assertEqual(snapshot["activation_summary"]["operator_topic_inbound"]["status"], "legacy")
        self.assertIn(snapshot["verification_status"], {"ok", "warn"})
        self.assertTrue(snapshot["next_steps"])
        self.assertNotIn("payload_json", snapshot["logs"][0])
        self.assertNotIn("payload_json", snapshot["audit_logs"][0])

    def test_web_login_and_api_smoke(self):
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["telegram"]["bot_token"] = "bot-secret"
        config_payload["telegram"]["operator_target"] = "-1003720752566:topic:2080"
        config_payload["okx"]["api_key"] = "api-key"
        config_payload["okx"]["api_secret"] = "api-secret"
        config_payload["okx"]["passphrase"] = "passphrase"
        config_payload["okx"]["enabled"] = False
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)

        status, headers, body = controller.route("GET", "/login")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")

        status, headers, body = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/api/state", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        state = body
        self.assertEqual(state["config"]["telegram"]["bot_token"], "")
        self.assertEqual(state["config"]["okx"]["api_secret"], "")
        self.assertTrue(state["secret_status"]["okx_demo_credentials_configured"])
        self.assertIn("capabilities", state)
        self.assertIn(state["verification_status"], {"ok", "warn"})
        self.assertTrue(state["next_steps"])
        self.assertIn("direct_use_text", state)
        self.assertIn("TG OKX Auto Trade Direct-Use Summary", state["direct_use_text"])
        self.assertEqual(state["capabilities"]["demo_only_guard"]["status"], "locked")
        self.assertIn("web_display", state)
        self.assertIn(state["web_display"]["verification_status"], {"通过", "警告"})
        self.assertIn("TG OKX Auto Trade 直接使用摘要", state["web_display"]["direct_use_text"])
        self.assertNotIn("Direct-Use Summary", state["web_display"]["direct_use_text"])
        self.assertTrue(any("核验本地就绪" in item for item in state["web_display"]["next_steps"]))
        readiness = {item["name"]: item for item in state["web_display"]["readiness_checks"]}
        self.assertEqual(readiness["web_auth"]["detail"], "已配置 6 位 Web PIN")
        self.assertIn(DEFAULT_DEMO_SIGNAL_TEXT, state["run_paths"]["inject_demo_signal_command"])

        status, _, payload = controller.route(
            "POST",
            "/api/inject-message",
            body=json.dumps(
                {"text": DEFAULT_DEMO_SIGNAL_TEXT, "chat_id": "-1001", "message_id": 77, "event_type": "new"}
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["orders"][0]["status"], "filled")
        self.assertEqual(payload["orders"][0]["payload"]["execution_path"], "simulated_demo")
        self.assertEqual(payload["orders"][0]["symbol"], "ADA-USDT-SWAP")

        status, _, payload = controller.route(
            "POST",
            "/api/inject-message",
            body=json.dumps(
                {"text": DEFAULT_DEMO_SIGNAL_TEXT, "chat_id": "-1001", "message_id": 78, "event_type": "new"}
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(len(payload["orders"]), 2)
        self.assertEqual(payload["orders"][-1]["status"], "filled")
        self.assertEqual(payload["messages"][0]["message_id"], 78)
        self.assertEqual(payload["messages"][1]["message_id"], 77)

        status, _, ready = controller.route("GET", "/readyz")
        self.assertEqual(status, 200)
        self.assertIn(ready["status"], {"ok", "warn"})

    def test_web_display_localizes_remaining_operator_facing_summary_text(self):
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["telegram"]["bot_token"] = ""
        config_payload["telegram"]["channels"] = []
        config_payload["telegram"]["operator_target"] = "-1003720752566:topic:2080"
        config_payload["okx"]["enabled"] = False
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")

        with mock.patch.dict(os.environ, {"TG_OKX_DISABLE_TOPIC_SEND": "1"}):
            runtime = Runtime(self.root / "config.json")
            self.addCleanup(runtime.stop)
            runtime.start(background=False)
            controller = WebController(runtime)

            status, headers, _ = controller.route(
                "POST",
                "/login",
                body=b"pin=123456",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self.assertEqual(status, 303)
            session_cookie = headers["Set-Cookie"]

            status, _, state = controller.route("GET", "/api/state", headers={"Cookie": session_cookie})
            self.assertEqual(status, 200)

        web_display = state["web_display"]
        readiness = {item["name"]: item for item in web_display["readiness_checks"]}
        capabilities = {item["name"]: item for item in web_display["capabilities"]}
        gaps = {item["id"]: item for item in web_display["remaining_gaps"]}
        localized_text = json.dumps(web_display, ensure_ascii=False)

        self.assertEqual(web_display["direct_use_profile"]["status_label"], "可手动直用")
        self.assertEqual(web_display["overview"]["runtime_detail"], "交易链路运行中")
        self.assertEqual(readiness["trading_runtime"]["detail"], "交易链路运行中")
        self.assertEqual(readiness["simulated_positions"]["detail"], "已恢复 0 份模拟持仓快照")
        self.assertIn("Web 登录、配置持久化、运行时状态和手动演示注入链路均已就绪", capabilities["manual_demo_pipeline"]["detail"])
        self.assertIn("如需执行真实操作员话题冒烟测试", capabilities["operator_topic"]["action"])
        self.assertIn("操作员话题出站发送已被", gaps["operator_topic_outbound"]["detail"])
        self.assertIn("若要期待操作员话题冒烟日志或运行时广播", gaps["operator_topic_outbound"]["action"])
        self.assertTrue(any("安全冒烟" in item for item in web_display["activation_checklist"]))
        self.assertTrue(any("操作员话题冒烟" in item for item in web_display["activation_checklist"]))
        self.assertIn("OKX 网关已就绪", web_display["health_json"])
        self.assertNotIn("OKX gateway ready", web_display["health_json"])
        self.assertEqual(web_display["overview"]["topic_target_source"], "操作员目标配置")
        self.assertIn("启用自动采集前，请替换成真实的 Telegram 公共网页地址。", web_display["setup_examples_json"])
        self.assertNotIn("Replace with the real public Telegram webpage", web_display["setup_examples_json"])
        self.assertNotIn("Trading pipeline is active", localized_text)
        self.assertNotIn("Web login, config persistence", localized_text)
        self.assertNotIn("Outbound operator-topic delivery is disabled", localized_text)
        self.assertNotIn("Web: open", localized_text)

    def test_web_display_localizes_remaining_okx_and_operator_topic_residues(self):
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["telegram"]["bot_token"] = ""
        config_payload["telegram"]["channels"] = []
        config_payload["telegram"]["operator_target"] = "-1003720752566:topic:2080"
        config_payload["okx"]["enabled"] = True
        config_payload["okx"]["api_key"] = "api-key"
        config_payload["okx"]["api_secret"] = "api-secret"
        config_payload["okx"]["passphrase"] = "passphrase"
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")

        with mock.patch("tg_okx_auto_trade.runtime.shutil.which", return_value="/usr/bin/openclaw"):
            runtime = Runtime(self.root / "config.json")
            self.addCleanup(runtime.stop)
            runtime.start(background=False)
            controller = WebController(runtime)

            status, headers, _ = controller.route(
                "POST",
                "/login",
                body=b"pin=123456",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self.assertEqual(status, 303)
            session_cookie = headers["Set-Cookie"]

            status, _, state = controller.route("GET", "/api/state", headers={"Cookie": session_cookie})
            self.assertEqual(status, 200)

        web_display = state["web_display"]
        capabilities = {item["name"]: item for item in web_display["capabilities"]}
        next_steps = "\n".join(web_display["next_steps"])
        localized_text = json.dumps(web_display, ensure_ascii=False)

        self.assertIn("已为 -1003720752566:topic:2080 配置操作员话题出站发送，但当前运行时还没有验证成功发送。", capabilities["operator_topic"]["detail"])
        self.assertIn("当前主支持的操作员流程是出站话题日志配合 Web/本地操作控制", capabilities["operator_topic"]["detail"])
        self.assertIn("当前已配置的 OKX 执行仅使用 OKX Demo REST 路径", next_steps)
        self.assertIn("交易链路运行中", web_display["health_json"])
        self.assertNotIn("Operator topic outbound delivery is configured for", localized_text)
        self.assertNotIn("Configured OKX execution uses the OKX demo REST path only", localized_text)
        self.assertNotIn("Trading pipeline is active", localized_text)
        self.assertNotIn("The intended operator flow is outbound topic logging plus Web/local operator controls", localized_text)

    def test_web_inject_endpoint_can_opt_into_real_okx_demo_path(self):
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["okx"]["enabled"] = True
        config_payload["okx"]["api_key"] = "api-key"
        config_payload["okx"]["api_secret"] = "api-secret"
        config_payload["okx"]["passphrase"] = "passphrase"
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        def fake_request(method, path, body=None):
            if path == "/api/v5/account/set-leverage":
                return {"code": "0", "msg": "", "data": [{"lever": body["lever"], "mgnMode": body["mgnMode"]}]}
            if path == "/api/v5/trade/order":
                return {"code": "0", "msg": "", "data": [{"ordId": "12345", "sCode": "0", "sMsg": ""}]}
            raise AssertionError(f"unexpected OKX path: {path}")

        with mock.patch.object(runtime.okx, "_request", side_effect=fake_request):
            status, _, payload = controller.route(
                "POST",
                "/api/inject-message",
                body=json.dumps(
                    {
                        "text": DEFAULT_DEMO_SIGNAL_TEXT,
                        "chat_id": "-1001",
                        "message_id": 78,
                        "event_type": "new",
                        "use_configured_okx_path": True,
                    }
                ).encode("utf-8"),
                headers={"Cookie": session_cookie, "Content-Type": "application/json"},
            )
        self.assertEqual(status, 201)
        self.assertEqual(payload["orders"][0]["payload"]["execution_path"], "real_demo_rest")

    def test_close_only_rejects_open_signal(self):
        self.runtime.update_config({"trading": {"readonly_close_only": True}})
        self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "RISK_REJECTED")
        self.assertEqual(snapshot["orders"], [])

    def test_invalid_intent_size_is_rejected(self):
        invalid_intent = TradingIntent(
            executable=True,
            action="open_long",
            symbol="BTC-USDT-SWAP",
            market_type="swap",
            side="buy",
            entry_type="market",
            size_mode="fixed_usdt",
            size_value=0.0,
            leverage=20,
            margin_mode="isolated",
            risk_level="medium",
            confidence=0.7,
            reason="invalid test",
        )
        with mock.patch.object(self.runtime.ai, "parse", return_value=invalid_intent):
            self.runtime.process_message(self._message("LONG BTCUSDT"))
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "RISK_REJECTED")
        self.assertEqual(snapshot["orders"], [])

    def test_invalid_intent_side_is_rejected(self):
        invalid_intent = TradingIntent(
            executable=True,
            action="reduce_short",
            symbol="BTC-USDT-SWAP",
            market_type="swap",
            side="sell",
            entry_type="market",
            size_mode="fixed_usdt",
            size_value=1.0,
            leverage=20,
            margin_mode="isolated",
            risk_level="medium",
            confidence=0.7,
            reason="invalid side test",
        )
        with mock.patch.object(self.runtime.ai, "parse", return_value=invalid_intent):
            self.runtime.process_message(self._message("REDUCE SHORT BTCUSDT"))
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["messages"][0]["status"], "RISK_REJECTED")
        self.assertEqual(snapshot["orders"], [])

    def test_runtime_hot_reloads_external_config_change(self):
        self.runtime.start(background=True)
        updated = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        updated["trading"]["default_leverage"] = 33
        updated["telegram"]["operator_target"] = "https://t.me/c/3720752566/2080"
        (self.root / "config.json").write_text(json.dumps(updated, indent=2), encoding="utf-8")
        deadline = time.time() + 4
        while time.time() < deadline:
            snapshot = self.runtime.snapshot()
            if (
                snapshot["config"]["trading"]["default_leverage"] == 33
                and snapshot["config"]["telegram"]["operator_target"] == "-1003720752566:topic:2080"
            ):
                break
            time.sleep(0.2)
        else:
            self.fail("runtime did not reload updated config.json within timeout")
        self.assertEqual(self.runtime.wiring_summary()["topic_target"], "-1003720752566:topic:2080")

    def test_telegram_watcher_increments_version_for_multiple_edits(self):
        watcher = self.runtime.telegram
        new_message = watcher._normalize_message("bot_api", "new", self._telegram_message(date=100))
        first_edit = watcher._normalize_message(
            "bot_api",
            "edit",
            self._telegram_message(date=100, edit_date=110, text="LONG BTCUSDT update 1"),
        )
        second_edit = watcher._normalize_message(
            "bot_api",
            "edit",
            self._telegram_message(date=100, edit_date=120, text="LONG BTCUSDT update 2"),
        )
        duplicate_edit = watcher._normalize_message(
            "bot_api",
            "edit",
            self._telegram_message(date=100, edit_date=120, text="LONG BTCUSDT update 2"),
        )

        self.assertEqual(new_message.version, 1)
        self.assertEqual(first_edit.version, 2)
        self.assertEqual(second_edit.version, 3)
        self.assertEqual(duplicate_edit.version, 3)

    def test_reconcile_now_surfaces_failure_without_raising(self):
        with mock.patch.object(self.runtime.telegram, "reconcile_once", side_effect=RuntimeError("boom")):
            summary = self.runtime.reconcile_now()
        self.assertEqual(summary["status"], "warn")
        self.assertIn("boom", summary["detail"])
        self.assertEqual(self.runtime.snapshot()["health"]["reconciliation"]["status"], "warn")

    def test_reconcile_counts_each_replayed_buffered_message(self):
        self.runtime.update_config({"telegram": {"bot_token": "demo-bot-token"}})
        history = [
            self._telegram_message(message_id=11, date=100, text="LONG BTCUSDT SIZE 1"),
            self._telegram_message(message_id=12, date=101, edit_date=111, text="SHORT BTCUSDT SIZE 2"),
        ]
        with mock.patch.object(self.runtime.telegram, "_get_chat_history", return_value=history):
            summary = self.runtime.reconcile_now()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["replayed_messages"], 2)
        snapshot = self.runtime.snapshot()
        self.assertEqual(len(snapshot["orders"]), 2)

    def test_reconcile_state_persists_across_fresh_runtime_snapshot(self):
        self.runtime.update_config({"telegram": {"bot_token": "demo-bot-token"}})
        history = [self._telegram_message(message_id=11, date=100, text="LONG BTCUSDT SIZE 1")]
        with mock.patch.object(self.runtime.telegram, "_get_chat_history", return_value=history):
            result = self.runtime.reconcile_now()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.runtime.public_snapshot()["operator_state"]["last_reconcile"]["status"], "ok")
        self.assertEqual(self.runtime.public_snapshot()["health"]["reconciliation"]["status"], "ok")

        fresh_runtime = Runtime(self.root / "config.json")
        self.addCleanup(fresh_runtime.stop)
        snapshot = fresh_runtime.public_snapshot()
        self.assertEqual(snapshot["operator_state"]["last_reconcile"]["status"], "ok")
        self.assertEqual(snapshot["operator_state"]["last_reconcile"]["detail"], result["detail"])
        self.assertEqual(snapshot["health"]["reconciliation"]["status"], "ok")
        self.assertEqual(snapshot["health"]["reconciliation"]["detail"], result["detail"])

    def test_topic_test_health_persists_across_fresh_runtime_snapshot(self):
        self.runtime.update_config({"telegram": {"operator_target": "-1003720752566:topic:2080"}})
        with mock.patch.object(
            self.runtime.topic_logger,
            "send",
            return_value={"sent": False, "status": "disabled", "reason": "mock disabled"},
        ):
            result = self.runtime.send_topic_test()
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(self.runtime.public_snapshot()["health"]["topic_logger"]["status"], "disabled")

        fresh_runtime = Runtime(self.root / "config.json")
        self.addCleanup(fresh_runtime.stop)
        snapshot = fresh_runtime.public_snapshot()
        self.assertEqual(snapshot["health"]["topic_logger"]["status"], "disabled")
        self.assertEqual(snapshot["health"]["topic_logger"]["detail"], "mock disabled")

    def test_readiness_warns_after_okx_and_topic_failures(self):
        self.runtime.update_config({"telegram": {"operator_target": "-1003720752566:topic:2080"}})
        self.runtime._set_health("okx_rest", "error", "bad credentials")
        self.runtime._set_health("topic_logger", "error", "network blocked")
        checks = {item["name"]: item for item in self.runtime.readiness_checks()}
        self.assertEqual(checks["okx_demo"]["status"], "warn")
        self.assertIn("bad credentials", checks["okx_demo"]["detail"])
        self.assertEqual(checks["topic_logger"]["status"], "warn")
        self.assertIn("network blocked", checks["topic_logger"]["detail"])
        self.assertEqual(checks["operator_commands"]["status"], "warn")

    def test_readiness_marks_operator_commands_as_legacy_even_when_bot_token_and_topic_exist(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "bot_token": "demo-bot-token",
                    "operator_target": "https://t.me/c/3720752566/2080",
                }
            }
        )
        checks = {item["name"]: item for item in self.runtime.readiness_checks()}
        self.assertEqual(checks["operator_commands"]["status"], "warn")
        self.assertIn("legacy/internal", checks["operator_commands"]["detail"])

    def test_web_pause_resume_and_topic_link_patch(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, paused = controller.route(
            "POST",
            "/api/actions/pause",
            body=json.dumps({"reason": "Test pause"}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(paused["paused"])

        status, _, updated = controller.route(
            "POST",
            "/api/config",
            body=json.dumps({"telegram": {"operator_target": "https://t.me/c/3720752566/2080"}}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["config"]["telegram"]["operator_target"], "-1003720752566:topic:2080")
        self.assertEqual(updated["config"]["telegram"]["operator_thread_id"], 2080)

        status, _, resumed = controller.route(
            "POST",
            "/api/actions/resume",
            body=json.dumps({"reason": "Test resume"}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(resumed["paused"])

    def test_web_config_patch_can_clear_stored_bot_token(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.update_config({"telegram": {"bot_token": "demo-bot-token"}})
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, updated = controller.route(
            "POST",
            "/api/config",
            body=json.dumps({"telegram": {"bot_token": ""}}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["config"]["telegram"]["bot_token"], "")
        self.assertFalse(updated["secret_status"]["telegram_bot_token_configured"])

    def test_web_ai_config_patch_updates_runtime(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, updated = controller.route(
            "POST",
            "/api/config",
            body=json.dumps(
                {
                    "ai": {
                        "provider": "heuristic",
                        "model": "intraday-v3",
                        "openclaw_agent_id": "swing-desk",
                        "thinking": "medium",
                        "timeout_seconds": 9,
                        "system_prompt": "Return strict JSON only.",
                    }
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["config"]["ai"]["model"], "intraday-v3")
        self.assertEqual(updated["config"]["ai"]["openclaw_agent_id"], "swing-desk")
        self.assertEqual(updated["config"]["ai"]["thinking"], "medium")
        self.assertEqual(updated["config"]["ai"]["timeout_seconds"], 9)
        self.assertEqual(updated["config"]["ai"]["system_prompt"], "Return strict JSON only.")
        self.assertEqual(runtime.snapshot()["config"]["ai"]["model"], "intraday-v3")

    def test_web_okx_config_patch_updates_editable_flags_and_env_names(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, updated = controller.route(
            "POST",
            "/api/config",
            body=json.dumps(
                {
                    "okx": {
                        "enabled": True,
                        "use_demo": True,
                        "rest_base": "https://demo.example.okx",
                        "ws_private_url": "wss://demo.example.okx/private",
                        "api_key_env": "WEB_OKX_KEY",
                        "api_secret_env": "WEB_OKX_SECRET",
                        "passphrase_env": "WEB_OKX_PASS",
                    }
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertIn("requires demo credentials", updated["error"])

        status, _, updated = controller.route(
            "POST",
            "/api/config",
            body=json.dumps(
                {
                    "okx": {
                        "enabled": False,
                        "use_demo": True,
                        "rest_base": "https://demo.example.okx",
                        "ws_private_url": "wss://demo.example.okx/private",
                        "api_key_env": "WEB_OKX_KEY",
                        "api_secret_env": "WEB_OKX_SECRET",
                        "passphrase_env": "WEB_OKX_PASS",
                    }
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["config"]["okx"]["rest_base"], "https://demo.example.okx")
        self.assertEqual(updated["config"]["okx"]["ws_private_url"], "wss://demo.example.okx/private")
        self.assertEqual(updated["config"]["okx"]["api_key_env"], "WEB_OKX_KEY")
        self.assertEqual(updated["config"]["okx"]["api_secret_env"], "WEB_OKX_SECRET")
        self.assertEqual(updated["config"]["okx"]["passphrase_env"], "WEB_OKX_PASS")

    def test_web_okx_credentials_route_writes_local_env_without_persisting_plaintext(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        runtime.update_config(
            {"okx": {"api_key_env": "WEB_OKX_KEY", "api_secret_env": "WEB_OKX_SECRET", "passphrase_env": "WEB_OKX_PASS"}}
        )
        status, _, updated = controller.route(
            "POST",
            "/api/okx-credentials",
            body=json.dumps(
                {
                    "api_key": "web-key",
                    "api_secret": "web-secret",
                    "passphrase": "web-passphrase",
                    "clear_existing": True,
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        env_values = read_env_file(local_env_path(self.root))
        self.assertEqual(env_values["WEB_OKX_KEY"], "web-key")
        self.assertEqual(env_values["WEB_OKX_SECRET"], "web-secret")
        self.assertEqual(env_values["WEB_OKX_PASS"], "web-passphrase")
        persisted = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["okx"]["api_key"], "")
        self.assertEqual(persisted["okx"]["api_secret"], "")
        self.assertEqual(persisted["okx"]["passphrase"], "")
        self.assertTrue(updated["secret_status"]["okx_demo_credentials_configured"])
        self.assertEqual(updated["secret_sources"]["okx_demo_credentials"], "env")

    def test_web_settings_page_surfaces_okx_and_ai_rework_in_chinese(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/?view=settings", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn("OKX 配置", body)
        self.assertIn("安全写入凭证", body)
        self.assertIn("常用项", body)
        self.assertIn("高级项", body)
        self.assertIn("当前生效值", body)

    def test_web_uses_feedback_bar_instead_of_alerts(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn('id="feedbackBar"', body)
        self.assertIn("function setFeedback(message, level='info', persist=false)", body)
        self.assertNotIn("alert(", body)

    def test_web_invalid_json_returns_bad_request(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, payload = controller.route(
            "POST",
            "/api/config",
            body=b"{",
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertIn("valid JSON", payload["error"])

    def test_web_root_redirects_to_login_and_login_redirects_back_when_authenticated(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)

        status, headers, payload = controller.route("GET", "/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")
        self.assertEqual(payload, "")

        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, headers, payload = controller.route("GET", "/login", headers={"Cookie": session_cookie})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertEqual(payload, "")

    def test_web_login_uses_env_pin_when_pin_hash_is_empty(self):
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["web"]["pin_hash"] = ""
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        (self.root / ".env").write_text("TG_OKX_WEB_PIN=123456\n", encoding="utf-8")

        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)

        status, headers, payload = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        session_cookie = headers["Set-Cookie"]

        status, _, payload = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn("Telegram OKX Auto Trade", payload)

    def test_web_login_returns_html_error_when_pin_is_not_configured(self):
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["web"]["pin_hash"] = ""
        config_payload["web"]["pin_plaintext_env"] = "TEST_ONLY_MISSING_WEB_PIN"
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        env_path = self.root / ".env"
        if env_path.exists():
            env_path.unlink()

        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)

        status, headers, payload = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 500)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("Missing web PIN", payload)
        self.assertIn("TEST_ONLY_MISSING_WEB_PIN", payload)

    def test_web_homepage_html_avoids_optional_chaining_and_nullish_coalescing(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, headers, body = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("Telegram OKX Auto Trade", body)
        self.assertNotIn("?.", body)
        self.assertNotIn("??", body)
        self.assertNotIn("`-100...:topic:...`", body)
        self.assertNotIn("`chat_id` accepts raw", body)
        self.assertIn("<code>-100...:topic:...</code>", body)
        self.assertIn("let latestLoadRequestId = 0;", body)
        self.assertIn("requestId !== latestLoadRequestId", body)
        self.assertIn("await load();", body)

    def test_web_homepage_operator_labels_are_substantially_chinese(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, login_body = controller.route("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("<h2>6 位 PIN</h2>", login_body)
        self.assertIn("<button>登录</button>", login_body)

        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn('id="primaryNav"', body)
        self.assertIn("总览 / Dashboard", body)
        self.assertIn("控制 / Actions", body)
        self.assertIn("配置 / Settings", body)
        self.assertIn("频道 / Channels", body)
        self.assertIn("运行数据 / Runtime Data", body)
        self.assertIn('id="viewMount"', body)
        self.assertIn("function renderOverviewView(ctx)", body)
        self.assertIn("function renderRuntimeView(ctx)", body)
        self.assertIn("displayTradingMode(data.config.trading.mode)", body)
        self.assertIn("displayExecutionMode(data.config.trading.execution_mode)", body)
        self.assertIn(">观察模式</option>", body)
        self.assertIn(">自动执行</option>", body)
        self.assertIn('<button id="channelSubmitButton" type="submit">保存频道</button>', body)
        self.assertIn("function setCurrentView(view, replace)", body)
        self.assertNotIn("data.config.trading.mode + ' / ' + data.config.trading.execution_mode", body)
        self.assertNotIn("Current Profile", body)
        self.assertNotIn("Verification", body)
        self.assertNotIn("Remaining Gaps", body)
        self.assertNotIn("Activation Summary", body)
        self.assertNotIn("Capabilities", body)
        self.assertNotIn("Next Steps", body)
        self.assertNotIn("<h2>激活摘要</h2>", body)
        self.assertNotIn("<h2>能力摘要</h2>", body)
        self.assertNotIn("<h2>剩余缺口</h2>", body)
        self.assertNotIn("<h2>就绪检查</h2>", body)
        self.assertNotIn("<h2>运行路径</h2>", body)
        self.assertNotIn("<h2>激活步骤</h2>", body)
        self.assertNotIn("<h2>直接使用摘要</h2>", body)

    def test_web_homepage_preserves_channel_form_state_during_refresh(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn("function captureChannelFormState()", body)
        self.assertIn("function restoreChannelFormState(state)", body)
        self.assertIn("restoreChannelFormState(channelFormState);", body)
        self.assertIn("channelForm.addEventListener('input', markDirty);", body)
        self.assertIn("if (getCurrentView() !== 'channels') return false;", body)

    def test_web_homepage_defers_background_refresh_while_channel_form_is_active(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn("function shouldDeferBackgroundLoad()", body)
        self.assertIn("if (background && shouldDeferBackgroundLoad()) return;", body)
        self.assertIn("if (getCurrentView() !== 'channels') return false;", body)
        self.assertIn("form.dataset.mode === 'edit' || form.dataset.dirty === 'true'", body)
        self.assertIn("form.contains(active)", body)
        self.assertIn("setInterval(() => { load({background:true}); }, 5000);", body)

    def test_web_homepage_channel_submit_button_stays_save_channel(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn('<button id="channelSubmitButton" type="submit">保存频道</button>', body)
        self.assertNotIn("Update Channel", body)

    def test_web_homepage_uses_view_shell_instead_of_single_big_page(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, body = controller.route("GET", "/", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn('id="statusStrip"', body)
        self.assertIn('id="viewMount"', body)
        self.assertIn('.form-section{display:grid;', body)
        self.assertIn('.field-grid{display:grid;', body)
        self.assertIn('.table-scroll{overflow:auto;', body)
        self.assertIn("const VIEW_RENDERERS =", body)
        self.assertIn("renderView(data, activeView);", body)
        self.assertIn("document.getElementById('viewMount').innerHTML = VIEW_RENDERERS[view](ctx);", body)
        self.assertNotIn("document.getElementById('app').innerHTML =", body)
        self.assertNotIn('<main id="app"></main>', body)

    def test_web_runtime_and_channels_views_remain_accessible_via_query_view(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, runtime_body = controller.route("GET", "/?view=runtime", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn('data-current-view="runtime"', runtime_body)
        self.assertIn("function renderRuntimeView(ctx)", runtime_body)
        self.assertIn("<h2>持仓</h2>", runtime_body)
        self.assertIn("<h2>健康状态</h2>", runtime_body)
        self.assertNotIn("<h2>运行路径</h2>", runtime_body)
        self.assertNotIn("<h2>激活步骤</h2>", runtime_body)
        self.assertNotIn("<h2>直接使用摘要</h2>", runtime_body)

        status, _, channels_body = controller.route("GET", "/?view=channels", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn('data-current-view="channels"', channels_body)
        self.assertIn('<div class="table-scroll"><table class="channel-table">', channels_body)
        self.assertIn('class="channel-actions"', channels_body)
        self.assertIn('data-channel-action="toggle"', channels_body)
        self.assertIn("自动采集优先用 <code>public_web</code>", channels_body)
        self.assertNotIn("header,header *{pointer-events:none}", channels_body)

    def test_runtime_operator_command_status_and_close(self):
        self.runtime.process_message(self._message("LONG BTCUSDT SIZE 1"))
        status_result = self.runtime.run_operator_command("/status", source="test")
        self.assertTrue(status_result["handled"])
        self.assertEqual(status_result["command"], "status")
        self.assertIn("[status]", status_result["reply"])

        readiness_result = self.runtime.run_operator_command("/readiness", source="test")
        self.assertEqual(readiness_result["status"], "ok")
        self.assertIn("[readiness]", readiness_result["reply"])

        paths_result = self.runtime.run_operator_command("/paths", source="test")
        self.assertEqual(paths_result["status"], "ok")
        self.assertIn("[paths]", paths_result["reply"])
        self.assertIn("runtime=", paths_result["reply"])

        channels_result = self.runtime.run_operator_command("/channels", source="test")
        self.assertEqual(channels_result["status"], "ok")
        self.assertIn("[channels]", channels_result["reply"])
        self.assertIn("test 已启用 bot_api", channels_result["reply"])

        signals_result = self.runtime.run_operator_command("/signals 2", source="test")
        self.assertEqual(signals_result["status"], "ok")
        self.assertIn("[signals]", signals_result["reply"])
        self.assertIn("EXECUTED", signals_result["reply"])

        risk_result = self.runtime.run_operator_command("/risk", source="test")
        self.assertEqual(risk_result["status"], "ok")
        self.assertIn("[risk]", risk_result["reply"])
        self.assertIn("global_tp_sl=False", risk_result["reply"])

        close_result = self.runtime.run_operator_command("/close BTC-USDT-SWAP", source="test")
        self.assertEqual(close_result["status"], "ok")
        self.assertFalse(close_result["push_reply"])
        positions = self.runtime.snapshot()["positions"]
        self.assertEqual(positions[0]["payload"]["side"], "flat")

    def test_process_operator_message_sends_reply_for_status_command(self):
        self.runtime.update_config({"telegram": {"operator_target": "https://t.me/c/3720752566/2080"}})
        message = NormalizedMessage.from_telegram("bot_api", "new", self._operator_telegram_message(text="/status"))
        with mock.patch.object(self.runtime, "_send_topic_update") as send_topic:
            self.runtime.process_operator_message(message)
        send_topic.assert_called_once()
        self.assertIn("[status]", send_topic.call_args.args[0])

    def test_telegram_watcher_routes_operator_topic_messages_to_operator_callback(self):
        self.runtime.update_config(
            {
                "telegram": {
                    "operator_target": "https://t.me/c/3720752566/2080",
                    "channels": [
                        {
                            "id": "test",
                            "name": "Test",
                            "source_type": "bot_api",
                            "chat_id": "-1001",
                            "channel_username": "",
                            "enabled": True,
                            "priority": 100,
                            "parse_profile_id": "default",
                            "strategy_profile_id": "default",
                            "risk_profile_id": "default",
                            "paper_trading_enabled": True,
                            "live_trading_enabled": False,
                            "listen_new_messages": True,
                            "listen_edits": True,
                            "listen_deletes": False,
                            "reconcile_interval_seconds": 30,
                            "dedup_window_seconds": 3600,
                            "notes": ""
                        }
                    ],
                }
            }
        )
        operator_callback = mock.Mock()
        message_callback = mock.Mock()
        self.runtime.telegram.operator_callback = operator_callback
        handled = self.runtime.telegram._process_update(
            {"update_id": 1, "message": self._operator_telegram_message(text="/pause hold")},
            message_callback,
            self.runtime.config_manager.get(),
        )
        self.assertTrue(handled)
        operator_callback.assert_called_once()
        message_callback.assert_not_called()

    def test_web_operator_command_endpoint(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, payload = controller.route(
            "POST",
            "/api/actions/operator-command",
            body=json.dumps({"text": "/status"}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["command"], "status")

    def test_web_reset_local_state_endpoint(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        runtime.process_message(self._message("LONG BTCUSDT SIZE 1"))
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, payload = controller.route(
            "POST",
            "/api/actions/reset-local-state",
            body=b"{}",
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["snapshot"]["orders"], [])
        self.assertEqual(payload["snapshot"]["messages"], [])
        self.assertEqual(payload["snapshot"]["positions"], [])
        self.assertIn("reset_local_state_command", payload["run_paths"])

    def test_web_channel_toggle_and_remove_endpoints(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

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
        self.assertEqual(status, 201)
        self.assertEqual(channel["id"], "chan-888")

        status, _, toggled = controller.route(
            "POST",
            "/api/channels/toggle",
            body=json.dumps({"channel_id": "chan-888", "enabled": False}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(toggled["enabled"])

        status, _, removed = controller.route(
            "POST",
            "/api/channels/remove",
            body=json.dumps({"channel_id": "chan-888"}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(removed["removed"])
        channels = [item["id"] for item in runtime.snapshot()["config"]["telegram"]["channels"]]
        self.assertNotIn("chan-888", channels)

    def test_web_channel_upsert_public_web_persists_and_chat_id_only_is_rejected(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, invalid_payload = controller.route(
            "POST",
            "/api/channels/upsert",
            body=json.dumps(
                {
                    "name": "Broken Public",
                    "source_type": "public_web",
                    "chat_id": "https://t.me/c/3720752566/2080",
                    "enabled": True,
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            invalid_payload["error"],
            "Channel chan-3720752566 must define channel_username when source_type=public_web",
        )

        status, _, channel = controller.route(
            "POST",
            "/api/channels/upsert",
            body=json.dumps(
                {
                    "name": "VIP Public",
                    "source_type": "public_web",
                    "chat_id": "https://t.me/c/3720752566/2080",
                    "channel_username": "https://t.me/s/lbeobhpreo",
                    "enabled": True,
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(channel["id"], "lbeobhpreo")
        self.assertEqual(channel["channel_username"], "lbeobhpreo")
        self.assertEqual(channel["chat_id"], "")
        persisted = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        saved = next(item for item in persisted["telegram"]["channels"] if item["id"] == "lbeobhpreo")
        self.assertEqual(saved["source_type"], "public_web")
        self.assertEqual(saved["channel_username"], "lbeobhpreo")
        self.assertEqual(saved["chat_id"], "")

    def test_web_channel_edit_persists_notes_and_records_audit(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, channel = controller.route(
            "POST",
            "/api/channels/upsert",
            body=json.dumps(
                {
                    "name": "VIP Public",
                    "source_type": "public_web",
                    "channel_username": "https://t.me/s/lbeobhpreo",
                    "enabled": True,
                    "notes": "before edit",
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)

        status, _, updated = controller.route(
            "POST",
            "/api/channels/upsert",
            body=json.dumps(
                {
                    "id": channel["id"],
                    "name": channel["name"],
                    "source_type": channel["source_type"],
                    "chat_id": channel["chat_id"],
                    "channel_username": channel["channel_username"],
                    "enabled": channel["enabled"],
                    "reconcile_interval_seconds": channel["reconcile_interval_seconds"],
                    "dedup_window_seconds": channel["dedup_window_seconds"],
                    "notes": "after edit",
                }
            ).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(updated["notes"], "after edit")

        snapshot = runtime.public_snapshot()
        saved = next(item for item in snapshot["config"]["telegram"]["channels"] if item["id"] == channel["id"])
        self.assertEqual(saved["notes"], "after edit")
        self.assertIn("Channel upserted", [item["message"] for item in snapshot["audit_logs"]])
        persisted = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        file_channel = next(item for item in persisted["telegram"]["channels"] if item["id"] == channel["id"])
        self.assertEqual(file_channel["notes"], "after edit")

    def test_web_channel_toggle_unknown_id_returns_bad_request(self):
        runtime = Runtime(self.root / "config.json")
        self.addCleanup(runtime.stop)
        runtime.start(background=False)
        controller = WebController(runtime)
        status, headers, _ = controller.route(
            "POST",
            "/login",
            body=b"pin=123456",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 303)
        session_cookie = headers["Set-Cookie"]

        status, _, payload = controller.route(
            "POST",
            "/api/channels/toggle",
            body=json.dumps({"channel_id": "missing", "enabled": False}).encode("utf-8"),
            headers={"Cookie": session_cookie, "Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertIn("Unknown channel id", payload["error"])

    def test_cli_operator_command_outputs_json(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "operator-command",
                "--config",
                str(self.root / "config.json"),
                "--text",
                "/status",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["handled"])
        self.assertEqual(payload["command"], "status")

    def test_cli_config_helpers_update_topic_and_channels(self):
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()

        set_topic = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "set-topic-target",
                "--config",
                str(self.root / "config.json"),
                "--target",
                "https://t.me/c/3720752566/2080",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(set_topic.returncode, 0, msg=set_topic.stderr)
        topic_payload = json.loads(set_topic.stdout)
        self.assertEqual(topic_payload["target"], "-1003720752566:topic:2080")
        self.assertEqual(topic_payload["operator_thread_id"], 2080)

        upsert_channel = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "upsert-channel",
                "--config",
                str(self.root / "config.json"),
                "--name",
                "CLI Public",
                "--channel-username",
                "https://t.me/s/lbeobhpreo",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(upsert_channel.returncode, 0, msg=upsert_channel.stderr)
        upsert_payload = json.loads(upsert_channel.stdout)
        self.assertEqual(upsert_payload["channel"]["chat_id"], "")
        self.assertEqual(upsert_payload["channel"]["channel_username"], "lbeobhpreo")
        self.assertEqual(upsert_payload["channel"]["id"], "lbeobhpreo")

        disable_channel = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "set-channel-enabled",
                "--config",
                str(self.root / "config.json"),
                "--channel-id",
                "lbeobhpreo",
                "--disabled",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(disable_channel.returncode, 0, msg=disable_channel.stderr)
        disable_payload = json.loads(disable_channel.stdout)
        self.assertFalse(disable_payload["channel"]["enabled"])

        remove_channel = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "remove-channel",
                "--config",
                str(self.root / "config.json"),
                "--channel-id",
                "lbeobhpreo",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(remove_channel.returncode, 0, msg=remove_channel.stderr)
        remove_payload = json.loads(remove_channel.stdout)
        self.assertEqual(remove_payload["status"], "ok")
        persisted = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["telegram"]["operator_target"], "-1003720752566:topic:2080")
        self.assertEqual(persisted["telegram"]["operator_thread_id"], 2080)
        self.assertFalse(any(item["id"] == "lbeobhpreo" for item in persisted["telegram"]["channels"]))

    def test_cli_runtime_actions_cover_pause_resume_reconcile_topic_close_and_reset(self):
        repo_root = Path(__file__).resolve().parents[1]
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["telegram"]["operator_target"] = "https://t.me/c/3720752566/2080"
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env["TG_OKX_DISABLE_TOPIC_SEND"] = "1"

        inject = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "inject-message",
                "--config",
                str(self.root / "config.json"),
                "--text",
                "LONG BTCUSDT SIZE 1",
                "--chat-id",
                "-1001",
                "--message-id",
                "901",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(inject.returncode, 0, msg=inject.stderr)

        pause = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "pause",
                "--config",
                str(self.root / "config.json"),
                "--reason",
                "CLI pause",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(pause.returncode, 0, msg=pause.stderr)
        pause_payload = json.loads(pause.stdout)
        self.assertTrue(pause_payload["operator_state"]["paused"])

        resume = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "resume",
                "--config",
                str(self.root / "config.json"),
                "--reason",
                "CLI resume",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(resume.returncode, 0, msg=resume.stderr)
        resume_payload = json.loads(resume.stdout)
        self.assertFalse(resume_payload["operator_state"]["paused"])

        reconcile = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "reconcile",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(reconcile.returncode, 0, msg=reconcile.stderr)
        self.assertEqual(json.loads(reconcile.stdout)["status"], "ok")

        snapshot_after_reconcile = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "snapshot",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(snapshot_after_reconcile.returncode, 0, msg=snapshot_after_reconcile.stderr)
        snapshot_payload = json.loads(snapshot_after_reconcile.stdout)
        self.assertEqual(snapshot_payload["operator_state"]["last_reconcile"]["status"], "ok")
        self.assertEqual(snapshot_payload["health"]["reconciliation"]["status"], "ok")

        topic_test = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "topic-test",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(topic_test.returncode, 0, msg=topic_test.stderr)
        topic_payload = json.loads(topic_test.stdout)
        self.assertEqual(topic_payload["status"], "disabled")

        snapshot_after_topic = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "snapshot",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(snapshot_after_topic.returncode, 0, msg=snapshot_after_topic.stderr)
        snapshot_payload = json.loads(snapshot_after_topic.stdout)
        self.assertEqual(snapshot_payload["health"]["topic_logger"]["status"], "disabled")

        close_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "close-positions",
                "--config",
                str(self.root / "config.json"),
                "--symbol",
                "BTC-USDT-SWAP",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(close_result.returncode, 0, msg=close_result.stderr)
        close_payload = json.loads(close_result.stdout)
        self.assertEqual(close_payload["closed"][0]["status"], "filled")

        reset_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "reset-local-state",
                "--config",
                str(self.root / "config.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(reset_result.returncode, 0, msg=reset_result.stderr)
        reset_payload = json.loads(reset_result.stdout)
        self.assertEqual(reset_payload["status"], "ok")
        self.assertEqual(reset_payload["snapshot"]["orders"], [])
        self.assertEqual(reset_payload["snapshot"]["messages"], [])
        self.assertEqual(reset_payload["snapshot"]["positions"], [])

    def test_cli_inject_defaults_to_simulated_even_with_okx_enabled(self):
        repo_root = Path(__file__).resolve().parents[1]
        config_payload = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config_payload["okx"]["enabled"] = True
        config_payload["okx"]["api_key"] = "api-key"
        config_payload["okx"]["api_secret"] = "api-secret"
        config_payload["okx"]["passphrase"] = "passphrase"
        (self.root / "config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
        env = os.environ.copy()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tg_okx_auto_trade.main",
                "inject-message",
                "--config",
                str(self.root / "config.json"),
                "--text",
                "LONG BTCUSDT SIZE 1",
                "--chat-id",
                "-1001",
                "--message-id",
                "902",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["orders"][0]["payload"]["execution_path"], "simulated_demo")


if __name__ == "__main__":
    unittest.main()
