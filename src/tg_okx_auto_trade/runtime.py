from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import shlex
import shutil
import threading
import time
from urllib.parse import urlparse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .ai import OpenClawAI
from .config import (
    AppConfig,
    ConfigManager,
    PROJECT_ROOT,
    chat_target_to_link,
    env_search_paths,
    local_env_path,
    merge_config_patch,
    normalize_channel_username,
    normalize_chat_id,
    public_config_dict,
    redact_sensitive_data,
    replace_config,
    resolve_okx_credentials,
    resolve_pin_hash,
    resolve_telegram_bot_token,
    resolve_topic_target,
    secret_sources,
    topic_target_parts,
    topic_target_to_link,
)
from .models import NormalizedMessage, TradingIntent, utc_now
from .okx import OKXGateway
from .risk import RiskEngine
from .storage import Storage
from .telegram import TelegramWatcher
from .topic_logger import TopicLogger


class EventStream:
    def __init__(self):
        self._events: list[dict[str, Any]] = []
        self._condition = threading.Condition()

    def publish(self, event: dict[str, Any]) -> None:
        with self._condition:
            self._events.append(event)
            if len(self._events) > 500:
                self._events = self._events[-500:]
            self._condition.notify_all()

    def snapshot(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._condition:
            return list(self._events[-limit:])

    def clear(self) -> None:
        with self._condition:
            self._events = []


class Runtime:
    def __init__(self, config_path: Path | None = None):
        self.config_manager = ConfigManager(config_path)
        config = self.config_manager.get()
        Path(config.runtime.data_dir).mkdir(parents=True, exist_ok=True)
        self.storage = Storage(config.runtime.sqlite_path)
        self.storage.upsert_channels([asdict(item) for item in config.telegram.channels])
        self.event_stream = EventStream()
        self._health_lock = threading.RLock()
        self._health = self._build_initial_health(config)
        self._reachability_lock = threading.RLock()
        self._reachability_cache: dict[str, dict[str, Any]] = {}
        self._web_bind_lock = threading.RLock()
        self._web_bind_host = ""
        self._web_bind_port = 0
        self._pause_reason = "Persisted paused state from config" if config.trading.paused else ""
        self._paused_at = utc_now() if config.trading.paused else ""
        self._last_resume_reason = ""
        self._last_resume_at = ""
        self._last_reconcile = {
            "status": "idle",
            "detail": "Reconciliation has not run yet",
            "retried_incomplete": 0,
            "replayed_messages": 0,
            "updated_at": utc_now(),
        }
        self._watcher_thread: threading.Thread | None = None
        self._reconcile_thread: threading.Thread | None = None
        self._config_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.telegram = TelegramWatcher(
            self.config_manager.get,
            self.log,
            self._update_telegram_health,
            self.process_operator_message,
        )
        self.okx = OKXGateway(config)
        self._started = False
        self._install_dependencies(config)
        self._restore_execution_state()
        self._sync_runtime_artifacts()

    def _install_dependencies(self, config: AppConfig) -> None:
        self.ai = OpenClawAI(config)
        self.risk = RiskEngine(config)
        self.okx.set_config(config)
        self.topic_logger = TopicLogger(config)

    def _refresh_trading_runtime_health(self, config: AppConfig, detail: str | None = None) -> None:
        if config.trading.paused:
            status = "paused"
            detail = detail or self._pause_reason or "Trading is paused"
        elif self._is_observe_only(config):
            status = "observe"
            detail = detail or "Observe-only path is active"
        else:
            status = "running"
            detail = detail or "Trading pipeline is active"
        if config.trading.readonly_close_only:
            detail = f"{detail}; close-only mode is active"
        self._set_health("trading_runtime", status, detail)

    def _restore_execution_state(self) -> None:
        self.okx.restore_simulated_state(
            self.storage.latest_positions(),
            counter=self.storage.max_demo_order_counter(),
        )
        self._set_health("okx_rest", "configured" if self.config_manager.get().okx.enabled else "simulated", "OKX gateway ready")
        self._refresh_trading_runtime_health(self.config_manager.get())
        topic_status, topic_detail = self._topic_health_state(self.config_manager.get())
        self._set_health("topic_logger", topic_status, topic_detail)

    def on_config_change(self, config: AppConfig) -> None:
        self._install_dependencies(config)
        self.storage.upsert_channels([asdict(item) for item in config.telegram.channels])
        self._set_health("openclaw_agent", config.ai.provider, f"provider={config.ai.provider}")
        self._set_health("okx_rest", "configured" if config.okx.enabled else "simulated", "OKX gateway ready")
        self._refresh_trading_runtime_health(config)
        topic_status, topic_detail = self._topic_health_state(config)
        self._set_health("topic_logger", topic_status, topic_detail)
        if not resolve_telegram_bot_token(config):
            self._set_health("telegram_watcher", "idle", "telegram.bot_token not configured")
        self.log("info", "config", "Configuration reloaded", {"mode": config.trading.mode}, audit=True)
        self._sync_runtime_artifacts()

    def start(self, background: bool = True) -> None:
        if self._started:
            return
        self._stop_event.clear()
        if background:
            self._watcher_thread = threading.Thread(target=self.telegram.run_forever, args=(self.process_message,), daemon=True)
            self._watcher_thread.start()
            self._reconcile_thread = threading.Thread(target=self._reconcile_loop, daemon=True)
            self._reconcile_thread.start()
            self._config_thread = threading.Thread(target=self._config_watch_loop, daemon=True)
            self._config_thread.start()
        self._started = True
        self._refresh_trading_runtime_health(self.config_manager.get())
        self.log("info", "system", "Runtime started", {"mode": self.config_manager.get().trading.mode}, audit=True)
        self._sync_runtime_artifacts()

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        self.telegram.stop()
        for thread in (self._watcher_thread, self._reconcile_thread, self._config_thread):
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
        self._watcher_thread = None
        self._reconcile_thread = None
        self._config_thread = None
        self._started = False
        self._sync_runtime_artifacts()

    def _reconcile_loop(self) -> None:
        while not self._stop_event.is_set():
            config = self.config_manager.get()
            self._run_reconcile_cycle(manual=False)
            time.sleep(max(30, min(channel.reconcile_interval_seconds for channel in config.telegram.channels)) if config.telegram.channels else 30)

    def _config_watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.config_manager.reload_if_changed():
                    self.on_config_change(self.config_manager.get())
            except Exception as exc:
                self.log("error", "config", "Configuration reload failed", {"error": str(exc)})
            time.sleep(self.config_manager.get().runtime.config_reload_seconds)

    def process_message(self, message: NormalizedMessage, *, force_simulated: bool = False) -> None:
        if not self.storage.save_message(message):
            self.log("info", "dedup", "Duplicate message version ignored", message.to_dict())
            self._sync_runtime_artifacts()
            return
        try:
            self._run_pipeline(message, force_simulated=force_simulated)
        finally:
            self._sync_runtime_artifacts()

    def _run_pipeline(self, message: NormalizedMessage, *, force_simulated: bool = False) -> None:
        self.storage.update_message_status(message.chat_id, message.message_id, message.version, "NORMALIZED")
        self.log("info", "telegram", "Message received", message.to_dict())
        config = self.config_manager.get()
        positions = self.okx.positions()
        recent_messages = self.storage.recent_messages(
            limit=5,
            chat_id=message.chat_id,
            exclude=(message.chat_id, message.message_id, message.version),
        )
        try:
            intent = self.ai.parse(message, recent_messages, {"positions": positions, "mode": config.trading.mode})
        except Exception as exc:
            self.storage.update_message_status(message.chat_id, message.message_id, message.version, "AI_FAILED")
            self.log("error", "ai", "AI parsing failed", {"error": str(exc), "chat_id": message.chat_id, "message_id": message.message_id})
            self._set_health("openclaw_agent", "error", str(exc))
            return
        self._set_health("openclaw_agent", config.ai.provider, f"provider={config.ai.provider}")
        self.storage.save_ai_decision(message, config.ai.model, config.ai.thinking, intent.to_dict())
        self.storage.update_message_status(message.chat_id, message.message_id, message.version, "AI_PARSED")
        self.log("info", "ai", "AI decision produced", intent.to_dict())
        duplicate_exists = self.storage.order_exists(self.risk._idempotency_key(message, intent))
        execution_intent = self._apply_global_protection(intent, config)
        risk = self.risk.evaluate(message, execution_intent, duplicate_exists)
        self.storage.save_risk_check(risk.idempotency_key, risk.approved, risk.code, risk.reason, risk.intent.to_dict())
        self.storage.update_message_status(message.chat_id, message.message_id, message.version, "RISK_CHECKED")
        if not risk.approved:
            self.storage.update_message_status(message.chat_id, message.message_id, message.version, "RISK_REJECTED")
            self.log("warn", "risk", risk.reason, {"code": risk.code, "idempotency_key": risk.idempotency_key})
            self._send_topic_update(f"[risk] {risk.code}: {risk.reason}")
            return
        if self._is_observe_only(config):
            self.storage.save_order(
                risk.idempotency_key,
                execution_intent,
                config.trading.mode,
                "observed",
                {
                    "environment": "observe_only",
                    "action": execution_intent.action,
                    "symbol": execution_intent.symbol,
                    "side": execution_intent.side,
                    "protection": _intent_protection_summary(execution_intent),
                    "reason": "Execution skipped because observe-only mode is active.",
                },
            )
            self.storage.update_message_status(message.chat_id, message.message_id, message.version, "OBSERVED")
            self.log("info", "execution", "Observe-only intent recorded", {"symbol": execution_intent.symbol, "action": execution_intent.action})
            self._send_topic_update(f"[trade:observe] {execution_intent.action} {execution_intent.symbol} -> observed")
            return
        try:
            result = self.okx.execute(execution_intent, force_simulated=force_simulated)
        except Exception as exc:
            self.storage.update_message_status(message.chat_id, message.message_id, message.version, "EXECUTION_FAILED")
            self.log("error", "execution", "Order execution failed", {"error": str(exc), "symbol": execution_intent.symbol, "action": execution_intent.action})
            self.pause_trading("OKX execution failure triggered automatic pause")
            self._set_health("okx_rest", "error", str(exc))
            self._send_topic_update(f"[execution:error] {execution_intent.action} {execution_intent.symbol} -> {exc}")
            return
        self._set_health("okx_rest", "configured" if config.okx.enabled else "simulated", "Last order path succeeded")
        self.storage.save_order(risk.idempotency_key, execution_intent, config.trading.mode, result.status, result.payload, result.exchange_order_id)
        if result.position_snapshot:
            self.storage.save_position_snapshot(execution_intent.symbol, result.position_snapshot)
        self.storage.update_message_status(message.chat_id, message.message_id, message.version, "EXECUTED")
        self.log("info", "execution", "Order executed", {"order_id": result.exchange_order_id, "status": result.status, "symbol": execution_intent.symbol})
        self._send_topic_update(f"[trade:{config.trading.mode}] {execution_intent.action} {execution_intent.symbol} -> {result.status} ({result.exchange_order_id})")

    def inject_message(
        self,
        text: str,
        chat_id: str,
        message_id: int,
        event_type: str = "new",
        version: int | None = None,
        use_configured_okx_path: bool = False,
    ) -> NormalizedMessage:
        body = text.strip()
        if not body:
            raise ValueError("Injected message text must not be empty")
        if event_type not in {"new", "edit"}:
            raise ValueError("event_type must be new or edit")
        timestamp = utc_now()
        digest = hashlib.sha256(
            json.dumps(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "event_type": event_type,
                    "version": version,
                    "text": body,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        message = NormalizedMessage(
            source="telegram",
            adapter="manual",
            chat_id=chat_id,
            message_id=message_id,
            event_type=event_type,
            version=version if version is not None else (2 if event_type == "edit" else 1),
            date=timestamp,
            edit_date=timestamp if event_type == "edit" else None,
            text=body,
            caption="",
            media=[],
            entities=[],
            reply_to=None,
            forward_from=None,
            raw_hash=digest,
            semantic_hash=digest,
        )
        self.process_message(message, force_simulated=not use_configured_okx_path)
        return message

    def _send_topic_update(self, text: str) -> None:
        result = self.topic_logger.send(text)
        target = resolve_topic_target(self.config_manager.get())
        if target and result.get("sent"):
            self._set_health("topic_logger", "ok", f"Last topic update sent to {result.get('target', target)}")
            return
        if target and result.get("status") == "disabled":
            self._set_health("topic_logger", "disabled", str(result.get("reason") or "Topic delivery disabled"))
            self.log("info", "telegram", "Topic update skipped", result)
            return
        if target and not result.get("sent"):
            self._set_health("topic_logger", "error", str(result.get("reason") or result.get("stderr") or "Topic delivery failed"))
            self.log("warn", "telegram", "Topic update failed", result)
            return
        self._set_health("topic_logger", "idle", self._topic_target_detail())

    def log(self, level: str, category: str, message: str, payload: dict[str, Any] | None = None, audit: bool = False) -> None:
        self.storage.log(level, category, message, payload, audit=audit)
        self.event_stream.publish(
            {
                "ts": utc_now(),
                "level": level,
                "category": category,
                "message": message,
                "payload": payload or {},
                "audit": audit,
            }
        )

    def authenticate(self, pin: str) -> str | None:
        config = self.config_manager.get()
        from .config import hash_pin

        expected = resolve_pin_hash(config)
        if hash_pin(pin) != expected:
            self.log("warn", "auth", "Failed web login", {"ip": "local"})
            return None
        session_id = self.storage.create_session()
        self.log("info", "auth", "Web login succeeded", {"session_id": session_id}, audit=True)
        return session_id

    def check_session(self, session_id: str) -> bool:
        return self.storage.touch_session(session_id)

    def snapshot(self) -> dict[str, Any]:
        config = self.config_manager.get()
        return {
            "config": config.to_dict(),
            "runtime": {
                "config_path": str(self.config_manager.path.resolve()),
                "data_dir": config.runtime.data_dir,
                "sqlite_path": config.runtime.sqlite_path,
            },
            "operator_state": {
                "paused": config.trading.paused,
                "pause_reason": self._pause_reason,
                "paused_at": self._paused_at,
                "last_resume_reason": self._last_resume_reason,
                "last_resume_at": self._last_resume_at,
                "last_reconcile": json.loads(json.dumps(self._last_reconcile)),
            },
            "dashboard": self.storage.dashboard_stats(),
            "messages": self.storage.latest_messages(25),
            "ai_decisions": self.storage.latest_ai_decisions(25),
            "orders": self.storage.latest_orders(50),
            "positions": self.storage.latest_positions(),
            "logs": self.storage.latest_logs(100),
            "audit_logs": self.storage.latest_audit_logs(50),
            "events": self.event_stream.snapshot(100),
            "health": self.health_snapshot(),
            "readiness_checks": self.readiness_checks(),
        }

    def secret_status(self, config: AppConfig | None = None) -> dict[str, bool]:
        current = config or self.config_manager.get()
        okx_key, okx_secret, okx_passphrase = resolve_okx_credentials(current)
        return {
            "web_pin_configured": bool(current.web.pin_hash or os.environ.get(current.web.pin_plaintext_env, "")),
            "telegram_bot_token_configured": bool(resolve_telegram_bot_token(current)),
            "okx_demo_credentials_configured": bool(okx_key and okx_secret and okx_passphrase),
            "topic_target_configured": bool(resolve_topic_target(current)),
        }

    def wiring_summary(self, config: AppConfig | None = None) -> dict[str, Any]:
        current = config or self.config_manager.get()
        enabled_channels = [channel for channel in current.telegram.channels if channel.enabled]
        enabled_bot_channels = [channel for channel in enabled_channels if channel.source_type == "bot_api"]
        enabled_mtproto_channels = [channel for channel in enabled_channels if channel.source_type == "mtproto"]
        telegram_bot_token = resolve_telegram_bot_token(current)
        action_support = self.okx.action_support()
        simulated_actions = action_support["simulated_demo"]
        configured_actions = action_support["real_demo_rest"] if current.okx.enabled else simulated_actions
        active_web_bind = self._active_web_bind()
        configured_web_bind = f"{current.web.host}:{current.web.port}"
        topic_target = resolve_topic_target(current)
        topic_health = self.health_snapshot().get("topic_logger", {})
        topic_chat_id, topic_thread_id = topic_target_parts(topic_target, current.telegram.operator_thread_id)
        if telegram_bot_token and enabled_bot_channels:
            telegram_mode = "bot_api_polling"
        elif enabled_bot_channels:
            telegram_mode = "bot_api_configured_without_token"
        elif enabled_mtproto_channels:
            telegram_mode = "mtproto_configured_not_implemented"
        else:
            telegram_mode = "idle"
        topic_source = "operator_target" if current.telegram.operator_target else ("report_topic" if current.telegram.report_topic else "")
        if not topic_target:
            operator_command_ingress = "not_configured"
        elif telegram_bot_token:
            operator_command_ingress = "ready"
        else:
            operator_command_ingress = "configured_without_bot_token"
        return {
            "telegram_watch_mode": telegram_mode,
            "enabled_channel_ids": [channel.id for channel in enabled_channels],
            "enabled_channel_targets": [
                channel.chat_id or channel.channel_username for channel in enabled_channels
            ],
            "topic_target": topic_target,
            "topic_target_link": topic_target_to_link(topic_target),
            "topic_chat_id": topic_chat_id,
            "topic_thread_id": topic_thread_id,
            "topic_target_source": topic_source,
            "topic_delivery_state": topic_health.get("status", "idle"),
            "topic_delivery_detail": topic_health.get("detail", self._topic_target_detail(current)),
            "topic_delivery_verified": topic_health.get("status") == "ok",
            "operator_command_ingress": operator_command_ingress,
            "topic_delivery_enabled": os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() != "1",
            "okx_execution_path": "real_demo_rest" if current.okx.enabled else "simulated_demo",
            "manual_signal_default_path": "simulated_demo",
            "manual_signal_configured_path": "real_demo_rest" if current.okx.enabled else "simulated_demo",
            "configured_okx_supported_actions": configured_actions,
            "configured_okx_unsupported_actions": [
                action for action in simulated_actions if action not in configured_actions
            ],
            "okx_rest_reachability": self._endpoint_reachability("okx_rest_base", current.okx.rest_base, enabled=current.okx.enabled),
            "trading_mode": current.trading.mode,
            "execution_mode": current.trading.execution_mode,
            "web_bind": active_web_bind or configured_web_bind,
            "web_server_active": bool(active_web_bind),
            "web_restart_required": bool(active_web_bind and active_web_bind != configured_web_bind),
        }

    def public_snapshot(self) -> dict[str, Any]:
        config = self.config_manager.get()
        snapshot = self.snapshot()
        usage_paths = self.usage_paths()
        snapshot["config"] = public_config_dict(config)
        snapshot["secret_status"] = self.secret_status(config)
        snapshot["secret_sources"] = secret_sources(config)
        snapshot["wiring"] = self.wiring_summary(config)
        snapshot["capabilities"] = self.capability_summary(config)
        snapshot["activation_summary"] = self.activation_summary(config)
        snapshot["remaining_gaps"] = self.remaining_gaps(config)
        snapshot["verification_status"] = self._verification_status(snapshot["readiness_checks"])
        snapshot["next_steps"] = self._verification_next_steps(config, usage_paths)
        return redact_sensitive_data(snapshot)

    def usage_paths(self) -> dict[str, Any]:
        config = self.config_manager.get()
        config_path = str(self.config_manager.path.resolve())
        quoted_config = shlex.quote(config_path)
        quoted_repo_root = shlex.quote(str(PROJECT_ROOT))
        local_env = local_env_path(self.config_manager.path.parent.resolve())
        project_env = env_search_paths(self.config_manager.path.parent.resolve())[-1]
        pin_env = config.web.pin_plaintext_env
        telegram_token_env = config.telegram.bot_token_env
        wiring = self.wiring_summary(config)
        configured_web_bind = f"{config.web.host}:{config.web.port}"
        active_web_bind = wiring["web_bind"]
        artifact_paths = self.runtime_artifact_paths(config)
        enabled_bot_channels = [
            channel for channel in config.telegram.channels if channel.enabled and channel.source_type == "bot_api"
        ]
        configured_channels = list(config.telegram.channels)
        web_login = f"http://{active_web_bind}/login"
        inject_demo_signal_command = (
            "python3 -m tg_okx_auto_trade.main inject-message "
            f"--config {quoted_config} --text 'LONG BTCUSDT now'"
        )
        inject_configured_demo_signal_command = (
            "python3 -m tg_okx_auto_trade.main inject-message "
            f"--config {quoted_config} --real-okx-demo --text 'LONG BTCUSDT now'"
        )
        topic_test_command = f"python3 -m tg_okx_auto_trade.main topic-test --config {quoted_config}"
        enabled_channel_links = [
            chat_target_to_link(channel.chat_id, channel.channel_username)
            for channel in config.telegram.channels
            if channel.enabled
        ]
        verify_demo_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "verify_demo.py").resolve()))
        smoke_config_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_config.py").resolve()))
        smoke_cli_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_cli.py").resolve()))
        smoke_runtime_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_runtime.py").resolve()))
        smoke_e2e_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_e2e.py").resolve()))
        smoke_web_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_web.py").resolve()))
        smoke_operator_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_operator.py").resolve()))
        smoke_telegram_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_telegram.py").resolve()))
        smoke_http_server_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_http_server.py").resolve()))
        smoke_okx_demo_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "smoke_okx_demo.py").resolve()))
        smoke_suite_script = shlex.quote(str((PROJECT_ROOT / "scripts" / "run_demo_suite.py").resolve()))
        if wiring["enabled_channel_ids"]:
            first_channel_id = wiring["enabled_channel_ids"][0]
        elif configured_channels:
            first_channel_id = configured_channels[0].id
        else:
            first_channel_id = ""
        channel_id_example = first_channel_id or "<channel-id-from-upsert-channel>"
        topic_target_example = wiring["topic_target_link"] or wiring["topic_target"] or "https://t.me/c/3720752566/2080"
        source_channel_example = {
            "id": "vip-btc",
            "name": "VIP BTC",
            "source_type": "bot_api",
            "chat_id": "-1001234567890",
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
            "notes": "Replace with the real Telegram source channel before enabling automatic ingestion.",
        }
        current_secret_sources = secret_sources(config)
        activation_checklist = [
            f"Web: open {web_login} and authenticate with the configured 6-digit PIN.",
            f"Safe smoke: run `{inject_demo_signal_command}` to validate the local simulated pipeline.",
        ]
        if current_secret_sources["telegram_bot_token"] == "config" or current_secret_sources["okx_demo_credentials"] == "config":
            activation_checklist.append(
                f"Prefer local secret storage in `{local_env}`. Use `{f'python3 -m tg_okx_auto_trade.main externalize-secrets --config {quoted_config}'}` to move inline Telegram/OKX secrets into `.env` without enabling live mode."
            )
        if config.okx.enabled:
            activation_checklist.append(
                f"Credentialed OKX demo: run `{inject_configured_demo_signal_command}` only when you intentionally want an OKX demo REST order."
            )
        if wiring["topic_target"]:
            activation_checklist.append(
                f"Operator topic smoke: run `{topic_test_command}` after confirming outbound Telegram delivery is allowed."
            )
        else:
            activation_checklist.append(
                "Optional operator topic: set `telegram.operator_target` to a topic target such as `https://t.me/c/3720752566/2080`."
            )
        if not resolve_telegram_bot_token(config):
            activation_checklist.append(
                f"Add `telegram.bot_token` locally or export `{telegram_token_env}` before expecting inbound operator-topic commands or Telegram polling."
            )
        if not enabled_bot_channels:
            activation_checklist.append(
                "Add at least one enabled `bot_api` source channel before expecting automatic source-channel ingestion."
            )
        return {
            "repo_root": str(PROJECT_ROOT),
            "repo_root_hint": f"cd {quoted_repo_root}",
            "config_path": config_path,
            "local_env_path": str(local_env),
            "project_env_path": str(project_env),
            "env_example_path": str((PROJECT_ROOT / ".env.example").resolve()),
            "runtime_state_dir": config.runtime.data_dir,
            "sqlite_path": config.runtime.sqlite_path,
            "runtime_direct_use_json": artifact_paths["runtime_direct_use_json"],
            "runtime_direct_use_text": artifact_paths["runtime_direct_use_text"],
            "runtime_public_state_json": artifact_paths["runtime_public_state_json"],
            "web_login": web_login,
            "healthz": f"http://{active_web_bind}/healthz",
            "readyz": f"http://{active_web_bind}/readyz",
            "curl_login_command": f"curl -i {shlex.quote(web_login)}",
            "curl_healthz_command": f"curl -s {shlex.quote(f'http://{active_web_bind}/healthz')}",
            "curl_readyz_command": f"curl -s {shlex.quote(f'http://{active_web_bind}/readyz')}",
            "configured_web_login": f"http://{configured_web_bind}/login",
            "verify_command": f"python3 -m tg_okx_auto_trade.main verify --config {quoted_config}",
            "paths_command": f"python3 -m tg_okx_auto_trade.main paths --config {quoted_config}",
            "direct_use_command": f"python3 -m tg_okx_auto_trade.main direct-use --config {quoted_config}",
            "snapshot_command": f"python3 -m tg_okx_auto_trade.main snapshot --config {quoted_config}",
            "serve_command": f"python3 -m tg_okx_auto_trade.main serve --config {quoted_config}",
            "inject_demo_signal_command": inject_demo_signal_command,
            "inject_configured_demo_signal_command": inject_configured_demo_signal_command,
            "pause_command": (
                "python3 -m tg_okx_auto_trade.main pause "
                f"--config {quoted_config} --reason 'Manual pause from CLI'"
            ),
            "resume_command": (
                "python3 -m tg_okx_auto_trade.main resume "
                f"--config {quoted_config} --reason 'Manual resume from CLI'"
            ),
            "reconcile_command": f"python3 -m tg_okx_auto_trade.main reconcile --config {quoted_config}",
            "topic_test_command": topic_test_command,
            "operator_command_command": (
                "python3 -m tg_okx_auto_trade.main operator-command "
                f"--config {quoted_config} --text '/status'"
            ),
            "reset_local_state_command": (
                "python3 -m tg_okx_auto_trade.main reset-local-state "
                f"--config {quoted_config}"
            ),
            "set_topic_target_command": (
                "python3 -m tg_okx_auto_trade.main set-topic-target "
                f"--config {quoted_config} --target {shlex.quote(topic_target_example)}"
            ),
            "upsert_channel_command": (
                "python3 -m tg_okx_auto_trade.main upsert-channel "
                f"--config {quoted_config} --name 'VIP BTC' --chat-id -1001234567890"
            ),
            "disable_channel_command": (
                "python3 -m tg_okx_auto_trade.main set-channel-enabled "
                f"--config {quoted_config} --channel-id {shlex.quote(channel_id_example)} --disabled"
            ),
            "remove_channel_command": (
                "python3 -m tg_okx_auto_trade.main remove-channel "
                f"--config {quoted_config} --channel-id {shlex.quote(channel_id_example)}"
            ),
            "channel_helper_target": first_channel_id,
            "close_positions_command": (
                "python3 -m tg_okx_auto_trade.main close-positions "
                f"--config {quoted_config} --symbol BTC-USDT-SWAP"
            ),
            "verify_demo_command": f"python3 {verify_demo_script} --config {quoted_config}",
            "smoke_config_command": f"python3 {smoke_config_script} --config {quoted_config}",
            "smoke_cli_command": f"python3 {smoke_cli_script} --config {quoted_config}",
            "smoke_runtime_command": f"python3 {smoke_runtime_script} --config {quoted_config}",
            "smoke_e2e_command": f"python3 {smoke_e2e_script} --config {quoted_config}",
            "smoke_web_command": f"python3 {smoke_web_script} --config {quoted_config}",
            "smoke_operator_command": f"python3 {smoke_operator_script} --config {quoted_config}",
            "smoke_telegram_command": f"python3 {smoke_telegram_script} --config {quoted_config}",
            "smoke_http_server_command": f"python3 {smoke_http_server_script} --config {quoted_config}",
            "smoke_okx_demo_command": f"python3 {smoke_okx_demo_script} --config {quoted_config}",
            "smoke_suite_command": f"python3 {smoke_suite_script} --config {quoted_config}",
            "init_config_command": (
                "python3 -m tg_okx_auto_trade.main init-config "
                f"--config {quoted_config} --pin 123456 --force"
            ),
            "externalize_secrets_command": (
                "python3 -m tg_okx_auto_trade.main externalize-secrets "
                f"--config {quoted_config}"
            ),
            "pin_env": pin_env,
            "telegram_bot_token_env": telegram_token_env,
            "okx_api_key_env": config.okx.api_key_env,
            "okx_api_secret_env": config.okx.api_secret_env,
            "okx_passphrase_env": config.okx.passphrase_env,
            "web_pin_source": "config.web.pin_hash" if config.web.pin_hash else f"${pin_env}",
            "secret_sources": secret_sources(config),
            "topic_target": wiring["topic_target"],
            "topic_target_source": wiring["topic_target_source"],
            "topic_target_link": wiring["topic_target_link"],
            "topic_chat_id": wiring["topic_chat_id"],
            "topic_thread_id": wiring["topic_thread_id"],
            "topic_delivery_state": wiring["topic_delivery_state"],
            "topic_delivery_detail": wiring["topic_delivery_detail"],
            "topic_delivery_verified": wiring["topic_delivery_verified"],
            "operator_command_ingress": wiring["operator_command_ingress"],
            "topic_delivery_enabled": wiring["topic_delivery_enabled"],
            "topic_target_input_example": "https://t.me/c/3720752566/2080",
            "source_channel_message_link_example": "https://t.me/c/1234567890/42",
            "telegram_watch_mode": wiring["telegram_watch_mode"],
            "okx_execution_path": wiring["okx_execution_path"],
            "manual_signal_default_path": wiring["manual_signal_default_path"],
            "manual_signal_configured_path": wiring["manual_signal_configured_path"],
            "configured_okx_supported_actions": wiring["configured_okx_supported_actions"],
            "configured_okx_unsupported_actions": wiring["configured_okx_unsupported_actions"],
            "enabled_channel_ids": wiring["enabled_channel_ids"],
            "enabled_channel_links": [link for link in enabled_channel_links if link],
            "web_bind": wiring["web_bind"],
            "web_server_active": wiring["web_server_active"],
            "web_restart_required": wiring["web_restart_required"],
            "channel_input_examples": [
                "-1001234567890",
                "@channel_name",
                "https://t.me/channel_name",
                "https://t.me/c/1234567890/42",
            ],
            "operator_command_examples": [
                "/help",
                "/status",
                "/readiness",
                "/paths",
                "/channels",
                "/signals 5",
                "/risk",
                "/positions",
                "/orders 5",
                "/pause demo hold",
                "/resume demo resume",
                "/close BTC-USDT-SWAP",
                "/topic-test",
            ],
            "activation_checklist": activation_checklist,
            "setup_examples": {
                "operator_target": topic_target_example,
                "source_channel": source_channel_example,
                "telegram_patch": {
                    "telegram": {
                        "bot_token": "<set-locally>",
                        "operator_target": topic_target_example,
                        "channels": [source_channel_example],
                    }
                },
                "okx_demo_patch": {
                    "okx": {
                        "enabled": True,
                        "use_demo": True,
                        "api_key": "<set-locally>",
                        "api_secret": "<set-locally>",
                        "passphrase": "<set-locally>",
                    }
                },
            },
        }

    def runtime_artifact_paths(self, config: AppConfig | None = None) -> dict[str, str]:
        current = config or self.config_manager.get()
        base_path = Path(current.runtime.data_dir)
        return {
            "runtime_direct_use_json": str((base_path / "direct-use.json").resolve()),
            "runtime_direct_use_text": str((base_path / "direct-use.txt").resolve()),
            "runtime_public_state_json": str((base_path / "public-state.json").resolve()),
        }

    def direct_use_payload(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        usage_paths: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        public_state = snapshot or self.public_snapshot()
        run_paths = usage_paths or self.usage_paths()
        return {
            "generated_at": utc_now(),
            "status": public_state.get("verification_status", "unknown"),
            "runtime": public_state.get("runtime", {}),
            "operator_state": public_state.get("operator_state", {}),
            "dashboard": public_state.get("dashboard", {}),
            "health": public_state.get("health", {}),
            "run_paths": run_paths,
            "wiring": public_state.get("wiring", {}),
            "capabilities": public_state.get("capabilities", {}),
            "activation_summary": public_state.get("activation_summary", {}),
            "remaining_gaps": public_state.get("remaining_gaps", []),
            "readiness_checks": public_state.get("readiness_checks", []),
            "next_steps": public_state.get("next_steps", []),
            "secret_status": public_state.get("secret_status", {}),
        }

    def direct_use_text(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        usage_paths: dict[str, Any] | None = None,
    ) -> str:
        return self._direct_use_text(self.direct_use_payload(snapshot=snapshot, usage_paths=usage_paths))

    def _sync_runtime_artifacts(self) -> None:
        try:
            config = self.config_manager.get()
            data_dir = Path(config.runtime.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
            public_state = self.public_snapshot()
            usage_paths = self.usage_paths()
            public_state["run_paths"] = usage_paths
            direct_use = self.direct_use_payload(snapshot=public_state, usage_paths=usage_paths)
            artifact_paths = self.runtime_artifact_paths(config)
            self._write_json_file(Path(artifact_paths["runtime_public_state_json"]), public_state)
            self._write_json_file(Path(artifact_paths["runtime_direct_use_json"]), direct_use)
            self._write_text_file(
                Path(artifact_paths["runtime_direct_use_text"]),
                self._direct_use_text(direct_use),
            )
        except Exception:
            return

    def _write_json_file(self, path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(path)

    def _write_text_file(self, path: Path, payload: str) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)

    def _direct_use_text(self, direct_use: dict[str, Any]) -> str:
        run_paths = direct_use.get("run_paths", {})
        capabilities = direct_use.get("capabilities", {})
        activation = direct_use.get("activation_summary", {})
        remaining_gaps = direct_use.get("remaining_gaps", [])
        next_steps = direct_use.get("next_steps", [])
        readiness_checks = direct_use.get("readiness_checks", [])
        lines = [
            "TG OKX Auto Trade Direct-Use Summary",
            f"generated_at: {direct_use.get('generated_at', '')}",
            f"status: {direct_use.get('status', 'unknown')}",
            "",
            "Current profile",
            f"- overall: {activation.get('overall_profile', {}).get('status', 'unknown')}",
            f"- manual_demo: {activation.get('manual_demo', {}).get('status', 'unknown')}",
            f"- configured_okx_demo: {activation.get('configured_okx_demo', {}).get('status', 'unknown')}",
            f"- automatic_telegram: {activation.get('automatic_telegram', {}).get('status', 'unknown')}",
            f"- operator_topic_outbound: {activation.get('operator_topic_outbound', {}).get('status', 'unknown')}",
            f"- operator_topic_inbound: {activation.get('operator_topic_inbound', {}).get('status', 'unknown')}",
            f"- demo_only_guard: {activation.get('demo_only_guard', {}).get('status', 'unknown')}",
            f"- profile_detail: {capabilities.get('current_operating_profile', {}).get('detail', '')}",
            f"- next_action: {capabilities.get('current_operating_profile', {}).get('action', '')}",
            "",
            "Paths",
            f"- repo_root: {run_paths.get('repo_root', '')}",
            f"- config_path: {run_paths.get('config_path', '')}",
            f"- local_env_path: {run_paths.get('local_env_path', '')}",
            f"- runtime_state_dir: {run_paths.get('runtime_state_dir', '')}",
            f"- sqlite_path: {run_paths.get('sqlite_path', '')}",
            f"- runtime_direct_use_json: {run_paths.get('runtime_direct_use_json', '')}",
            f"- runtime_direct_use_text: {run_paths.get('runtime_direct_use_text', '')}",
            f"- runtime_public_state_json: {run_paths.get('runtime_public_state_json', '')}",
            f"- web_login: {run_paths.get('web_login', '')}",
            f"- healthz: {run_paths.get('healthz', '')}",
            f"- readyz: {run_paths.get('readyz', '')}",
            f"- topic_target: {run_paths.get('topic_target_link') or run_paths.get('topic_target', '')}",
            f"- topic_delivery: {run_paths.get('topic_delivery_state', 'unknown')} ({run_paths.get('topic_delivery_detail', '')})",
            "",
            "Direct commands",
            f"- verify: {run_paths.get('verify_command', '')}",
            f"- paths: {run_paths.get('paths_command', '')}",
            f"- serve: {run_paths.get('serve_command', '')}",
            f"- snapshot: {run_paths.get('snapshot_command', '')}",
            f"- inject_demo: {run_paths.get('inject_demo_signal_command', '')}",
            f"- inject_configured_demo: {run_paths.get('inject_configured_demo_signal_command', '')}",
            f"- externalize_secrets: {run_paths.get('externalize_secrets_command', '')}",
            f"- topic_test: {run_paths.get('topic_test_command', '')}",
            f"- operator_status: {run_paths.get('operator_command_command', '')}",
            f"- reset_local_state: {run_paths.get('reset_local_state_command', '')}",
            f"- close_positions: {run_paths.get('close_positions_command', '')}",
            "",
            "Key capability details",
            f"- manual_demo: {capabilities.get('manual_demo_pipeline', {}).get('detail', '')}",
            f"- okx_demo: {capabilities.get('okx_demo_execution', {}).get('detail', '')}",
            f"- telegram_ingestion: {capabilities.get('telegram_ingestion', {}).get('detail', '')}",
            f"- operator_topic: {capabilities.get('operator_topic', {}).get('detail', '')}",
            f"- topic_delivery_state: {run_paths.get('topic_delivery_state', 'unknown')}",
            "",
            "Readiness warnings",
        ]
        warnings = [item for item in readiness_checks if item.get("status") != "pass"]
        if warnings:
            lines.extend(
                f"- {item.get('name', 'unknown')}: {item.get('status', 'unknown')} {item.get('detail', '')}"
                for item in warnings[:8]
            )
        else:
            lines.append("- none")
        lines.append("")
        lines.append("Remaining gaps")
        if remaining_gaps:
            lines.extend(
                f"- {item.get('id', 'unknown')}: {item.get('status', 'unknown')} {item.get('detail', '')}"
                for item in remaining_gaps[:10]
            )
        else:
            lines.append("- none")
        lines.append("")
        lines.append("Next steps")
        if next_steps:
            lines.extend(f"- {step}" for step in next_steps[:10])
        else:
            lines.append("- none")
        lines.append("")
        lines.append("This summary is redacted and demo-only. Live trading stays disabled.")
        return "\n".join(lines) + "\n"

    def capability_summary(self, config: AppConfig | None = None) -> dict[str, dict[str, str]]:
        current = config or self.config_manager.get()
        enabled_channels = [channel for channel in current.telegram.channels if channel.enabled]
        enabled_bot_channels = [channel for channel in enabled_channels if channel.source_type == "bot_api"]
        topic_target = resolve_topic_target(current)
        telegram_bot_token = resolve_telegram_bot_token(current)
        openclaw_path = shutil.which("openclaw")
        health = self.health_snapshot()
        okx_reachability = self._endpoint_reachability("okx_rest_base", current.okx.rest_base, enabled=current.okx.enabled)

        try:
            resolve_pin_hash(current)
        except Exception as exc:
            manual_demo_status = {
                "status": "blocked",
                "detail": str(exc),
                "action": "Set a 6-digit Web PIN before relying on Web or CLI control paths.",
            }
        else:
            manual_demo_status = {
                "status": "ready",
                "detail": (
                    "Web login, config persistence, runtime state, and manual demo injection paths are ready. "
                    "Manual signal injection defaults to the simulated engine even when OKX demo REST is configured."
                ),
                "action": f"Open {self.usage_paths()['web_login']} or use the inject-message CLI command for a safe demo signal.",
            }

        if health.get("okx_rest", {}).get("status") == "error":
            okx_status = {
                "status": "error",
                "detail": f"Last OKX demo execution failed: {health['okx_rest']['detail']}",
                "action": "Fix the OKX demo credential or network issue, then resume trading before relying on automatic execution.",
            }
        elif current.okx.enabled and okx_reachability["status"] != "reachable":
            okx_status = {
                "status": "partial",
                "detail": (
                    "Real OKX demo REST execution is configured, but the endpoint reachability check is not healthy: "
                    f"{okx_reachability['detail']}"
                ),
                "action": "Fix local DNS/network reachability to the OKX demo REST endpoint, then rerun the OKX demo smoke test.",
            }
        elif current.okx.enabled:
            okx_status = {
                "status": "ready",
                "detail": (
                    "Real OKX demo REST execution is configured. Orders remain restricted to demo/simulated trading only. "
                    f"Configured REST coverage: {', '.join(self.wiring_summary(current)['configured_okx_supported_actions'])}."
                ),
                "action": "Use a small demo-only signal path or the credentialed OKX demo smoke test to validate exchange execution.",
            }
        else:
            okx_status = {
                "status": "simulated",
                "detail": "The local simulated OKX demo engine is active. No real OKX demo REST calls will be made.",
                "action": "Set okx.enabled=true with demo credentials when you want the real OKX demo REST path.",
            }

        missing_ingestion: list[str] = []
        if not telegram_bot_token:
            missing_ingestion.append("telegram.bot_token")
        if not enabled_bot_channels:
            missing_ingestion.append("enabled bot_api channel")
        if missing_ingestion:
            telegram_status = {
                "status": "blocked",
                "detail": "Live Telegram ingestion is not ready: missing " + " + ".join(missing_ingestion) + ".",
                "action": (
                    "Configure Telegram Wiring, add at least one enabled bot_api channel in Web > Channels or config.telegram.channels, "
                    f"and provide the bot token via config or {current.telegram.bot_token_env}."
                ),
            }
        else:
            telegram_status = {
                "status": "ready",
                "detail": f"Telegram bot polling is ready for {len(enabled_bot_channels)} enabled bot_api channel(s).",
                "action": "Start the runtime and watch for live new/edit events from the configured channels.",
            }

        if not topic_target:
            topic_status = {
                "status": "blocked",
                "detail": "Operator topic logging is not configured.",
                "action": "Set telegram.operator_target or telegram.report_topic; topic links like https://t.me/c/<chat>/<topic> are accepted.",
            }
        elif os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() == "1":
            topic_status = {
                "status": "disabled",
                "detail": f"Operator topic target {topic_target} is configured, but delivery is disabled by TG_OKX_DISABLE_TOPIC_SEND=1.",
                "action": "Unset TG_OKX_DISABLE_TOPIC_SEND before running a real operator-topic smoke test.",
            }
        elif not openclaw_path:
            topic_status = {
                "status": "blocked",
                "detail": f"Operator topic target {topic_target} is configured, but the openclaw CLI is unavailable.",
                "action": "Install or expose the openclaw CLI before expecting topic delivery.",
            }
        elif health.get("topic_logger", {}).get("status") == "error":
            topic_status = {
                "status": "error",
                "detail": f"Last operator topic attempt failed: {health['topic_logger']['detail']}",
                "action": "Validate Telegram delivery/network access, then rerun the topic smoke action.",
            }
        else:
            inbound_detail = (
                "Inbound operator commands are available when the same Telegram bot is allowed to receive topic messages."
                if telegram_bot_token
                else "Outbound topic delivery is wired, and inbound operator commands are implemented, but telegram.bot_token is still missing for real topic-side control."
            )
            outbound_detail = (
                f"Operator topic outbound delivery to {topic_target} has already been verified in this runtime."
                if health.get("topic_logger", {}).get("status") == "ok"
                else f"Operator topic outbound delivery is configured for {topic_target}, but it has not been verified yet in this runtime."
            )
            topic_status = {
                "status": "ready" if telegram_bot_token else "partial",
                "detail": f"{outbound_detail} {inbound_detail}",
                "action": (
                    "Use the Web 'Topic Smoke' button, runtime.send_topic_test(), or the CLI topic-test command to verify outbound delivery."
                    if telegram_bot_token
                    else f"Add telegram.bot_token or export {current.telegram.bot_token_env} and put the bot in the operator topic chat to use inbound topic-side commands such as /status and /pause."
                ),
            }

        demo_guard_status = {
            "status": "locked",
            "detail": "Live trading is hard-disabled by config validation and runtime guards in this build.",
            "action": "Keep all validation in demo or simulated mode only.",
        }

        if manual_demo_status["status"] != "ready":
            return {
                "current_operating_profile": {
                    "status": "blocked",
                    "detail": manual_demo_status["detail"],
                    "action": manual_demo_status["action"],
                },
                "manual_demo_pipeline": manual_demo_status,
                "okx_demo_execution": okx_status,
                "telegram_ingestion": telegram_status,
                "operator_topic": topic_status,
                "demo_only_guard": demo_guard_status,
            }

        direct_use_missing: list[str] = []
        if not telegram_bot_token:
            direct_use_missing.append("telegram.bot_token")
        if not enabled_bot_channels:
            direct_use_missing.append("enabled bot_api source channel")
        if not topic_target:
            direct_use_missing.append("operator topic target")
        if current.trading.paused:
            direct_use_profile = {
                "status": "attention",
                "detail": (
                    "Web, config persistence, and manual demo controls are wired, but trading is currently paused. "
                    f"Outstanding automatic-ingestion prerequisites: {', '.join(direct_use_missing) if direct_use_missing else 'none'}."
                ),
                "action": "Fix the pause reason and resume trading before relying on the configured demo profile.",
            }
        elif direct_use_missing:
            direct_use_profile = {
                "status": "manual_ready",
                "detail": (
                    "The current profile is ready for direct manual/demo use: Web login, config edits, runtime artifacts, "
                    "manual demo injection, and the configured operator/OKX paths that do not require inbound Telegram. "
                    f"Full always-on automation is still blocked by: {', '.join(direct_use_missing)}."
                ),
                "action": "Use the manual demo path now, and add the missing Telegram/topic wiring before expecting full automatic signal flow.",
            }
        else:
            direct_use_profile = {
                "status": "ready",
                "detail": (
                    "Web control, Telegram bot_api ingestion, operator-topic wiring, and demo-only execution are all configured "
                    "for direct use in this build."
                ),
                "action": "Keep validation on demo/simulated paths only and start the runtime for live source-channel monitoring.",
            }

        return {
            "current_operating_profile": direct_use_profile,
            "manual_demo_pipeline": manual_demo_status,
            "okx_demo_execution": okx_status,
            "telegram_ingestion": telegram_status,
            "operator_topic": topic_status,
            "demo_only_guard": demo_guard_status,
        }

    def activation_summary(self, config: AppConfig | None = None) -> dict[str, dict[str, str]]:
        current = config or self.config_manager.get()
        capabilities = self.capability_summary(current)
        topic_target = resolve_topic_target(current)
        telegram_bot_token = resolve_telegram_bot_token(current)
        openclaw_path = shutil.which("openclaw")
        topic_health = self.health_snapshot().get("topic_logger", {})
        topic_env_disabled = os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() == "1"

        if not topic_target:
            operator_outbound = {
                "status": "blocked",
                "detail": "No operator topic target is configured for outbound logs.",
                "action": "Set telegram.operator_target or telegram.report_topic before expecting operator-topic delivery.",
            }
        elif topic_env_disabled:
            operator_outbound = {
                "status": "disabled",
                "detail": f"Outbound operator-topic delivery to {topic_target} is disabled by TG_OKX_DISABLE_TOPIC_SEND=1.",
                "action": "Unset TG_OKX_DISABLE_TOPIC_SEND before running a real topic smoke test.",
            }
        elif not openclaw_path:
            operator_outbound = {
                "status": "blocked",
                "detail": f"Outbound operator-topic delivery to {topic_target} is configured, but the openclaw CLI is unavailable.",
                "action": "Install or expose the openclaw CLI before expecting topic delivery.",
            }
        elif topic_health.get("status") == "error":
            operator_outbound = {
                "status": "error",
                "detail": f"Last outbound operator-topic attempt failed: {topic_health.get('detail', 'unknown error')}",
                "action": "Fix topic delivery/network access, then rerun the topic smoke action.",
            }
        elif topic_health.get("status") == "ok":
            operator_outbound = {
                "status": "ready",
                "detail": f"Outbound operator-topic delivery to {topic_target} has been verified in this runtime.",
                "action": "Continue using topic-test or the Web Topic Smoke action after wiring changes to confirm delivery still works.",
            }
        else:
            operator_outbound = {
                "status": "configured",
                "detail": f"Outbound operator-topic delivery is configured for {topic_target}, but this runtime has not verified a successful send yet.",
                "action": "Use topic-test or the Web Topic Smoke action to verify outbound delivery.",
            }

        if not topic_target:
            operator_inbound = {
                "status": "blocked",
                "detail": "Inbound operator commands are unavailable because no operator topic target is configured.",
                "action": "Set telegram.operator_target or telegram.report_topic before relying on Telegram-side operator commands.",
            }
        elif not telegram_bot_token:
            operator_inbound = {
                "status": "blocked",
                "detail": "Inbound operator commands are implemented, but telegram.bot_token is still missing.",
                "action": f"Add telegram.bot_token or export {current.telegram.bot_token_env} and allow the bot to receive topic messages.",
            }
        else:
            operator_inbound = {
                "status": "ready",
                "detail": "Inbound operator commands can be received through the configured Telegram bot/topic wiring.",
                "action": "Use /status, /readiness, /pause, /resume, /close, or /topic-test from the operator topic.",
            }

        return {
            "overall_profile": capabilities["current_operating_profile"],
            "manual_demo": capabilities["manual_demo_pipeline"],
            "configured_okx_demo": capabilities["okx_demo_execution"],
            "automatic_telegram": capabilities["telegram_ingestion"],
            "operator_topic_outbound": operator_outbound,
            "operator_topic_inbound": operator_inbound,
            "demo_only_guard": capabilities["demo_only_guard"],
        }

    def remaining_gaps(self, config: AppConfig | None = None) -> list[dict[str, str]]:
        current = config or self.config_manager.get()
        enabled_channels = [channel for channel in current.telegram.channels if channel.enabled]
        enabled_bot_channels = [channel for channel in enabled_channels if channel.source_type == "bot_api"]
        telegram_bot_token = resolve_telegram_bot_token(current)
        topic_target = resolve_topic_target(current)
        topic_health = self.health_snapshot().get("topic_logger", {})
        topic_env_disabled = os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() == "1"
        openclaw_path = shutil.which("openclaw")
        gaps: list[dict[str, str]] = []
        okx_reachability = self._endpoint_reachability("okx_rest_base", current.okx.rest_base, enabled=current.okx.enabled)

        if not telegram_bot_token:
            gaps.append(
                {
                    "id": "telegram_bot_token",
                    "scope": "telegram",
                    "status": "open",
                    "detail": "telegram.bot_token is not configured, so live Telegram polling is inactive.",
                    "action": f"Add a bot token in config or export {current.telegram.bot_token_env} before expecting automatic source-channel ingestion.",
                }
            )
        if not enabled_bot_channels:
            gaps.append(
                {
                    "id": "telegram_source_channel",
                    "scope": "telegram",
                    "status": "open",
                    "detail": "No enabled bot_api source channel is configured yet.",
                    "action": "Add at least one enabled bot_api channel in Web > Channels or config.telegram.channels.",
                }
            )
        if any(channel.source_type == "mtproto" and channel.enabled for channel in enabled_channels):
            gaps.append(
                {
                    "id": "telegram_mtproto",
                    "scope": "telegram",
                    "status": "partial",
                    "detail": "MTProto channels can be stored in config, but active MTProto watching is not implemented in this build.",
                    "action": "Use bot_api channels for active watching, or add a Telethon/MTProto adapter before relying on MTProto sources.",
                }
            )
        delete_channels = [
            channel.id
            for channel in enabled_bot_channels
            if channel.listen_deletes
        ]
        if delete_channels:
            gaps.append(
                {
                    "id": "telegram_delete_events",
                    "scope": "telegram",
                    "status": "partial",
                    "detail": (
                        "Some enabled bot_api channels request delete/revoke handling, "
                        "but Telegram delete events are not implemented in this build: "
                        + ", ".join(delete_channels)
                        + "."
                    ),
                    "action": "Keep delete/revoke expectations off for now, or add an adapter that can surface delete events before relying on that path.",
                }
            )
        gaps.append(
            {
                "id": "telegram_reconcile_history",
                "scope": "telegram",
                "status": "partial",
                "detail": "Bot API reconciliation only replays the in-process recent buffer; it does not backfill true channel history after downtime.",
                "action": "Keep the runtime continuously connected for now, or add a stronger history source before treating reconciliation as authoritative.",
            }
        )
        gaps.append(
            {
                "id": "okx_private_ws",
                "scope": "okx",
                "status": "partial",
                "detail": "OKX private WebSocket/account sync is not implemented in this build; real demo REST execution uses locally expected state after fills.",
                "action": "Stay on demo/simulated validation, or add private WS/account polling before promoting beyond this build.",
            }
        )
        if current.okx.enabled:
            unsupported_actions = self.wiring_summary(current)["configured_okx_unsupported_actions"]
            if unsupported_actions:
                gaps.append(
                    {
                        "id": "okx_demo_action_coverage",
                        "scope": "okx",
                        "status": "partial",
                        "detail": (
                            "Configured OKX demo REST execution supports open/add/reduce/reverse/close/cancel flows, "
                            "but still relies on the simulated engine for "
                            + ", ".join(unsupported_actions)
                            + " because this build only tracks attached protection locally and does not keep private WS/account sync."
                        ),
                        "action": "Use the simulated path for those actions, or extend the OKX REST implementation before relying on credentialed demo execution for them.",
                    }
                )
        if current.okx.enabled and okx_reachability["status"] != "reachable":
            gaps.append(
                {
                    "id": "okx_rest_connectivity",
                    "scope": "okx",
                    "status": "attention",
                    "detail": (
                        "OKX demo REST credentials are configured, but the configured endpoint is not currently reachable: "
                        f"{okx_reachability['detail']}"
                    ),
                    "action": "Fix local DNS/network access to the OKX REST host, then rerun the OKX demo smoke test before relying on credentialed demo execution.",
                }
            )
        if not topic_target:
            gaps.append(
                {
                    "id": "operator_topic",
                    "scope": "telegram",
                    "status": "open",
                    "detail": "Operator topic logging is not configured.",
                    "action": "Set telegram.operator_target or telegram.report_topic to enable operator-topic logs.",
                }
            )
        else:
            if topic_env_disabled:
                gaps.append(
                    {
                        "id": "operator_topic_outbound",
                        "scope": "telegram",
                        "status": "disabled",
                        "detail": "Outbound operator-topic delivery is disabled by TG_OKX_DISABLE_TOPIC_SEND=1.",
                        "action": "Unset TG_OKX_DISABLE_TOPIC_SEND before expecting operator-topic smoke logs or runtime broadcasts.",
                    }
                )
            elif not openclaw_path:
                gaps.append(
                    {
                        "id": "operator_topic_outbound",
                        "scope": "telegram",
                        "status": "open",
                        "detail": f"Outbound operator-topic delivery is configured for {topic_target}, but the openclaw CLI is unavailable.",
                        "action": "Install or expose the openclaw CLI before relying on operator-topic delivery.",
                    }
                )
            elif topic_health.get("status") == "error":
                gaps.append(
                    {
                        "id": "operator_topic_outbound",
                        "scope": "telegram",
                        "status": "attention",
                        "detail": f"Last outbound operator-topic attempt failed: {topic_health.get('detail', 'unknown error')}",
                        "action": "Fix topic delivery/network access, then rerun the topic smoke action before relying on operator-topic delivery.",
                    }
                )
            if not telegram_bot_token:
                gaps.append(
                    {
                        "id": "operator_topic_inbound",
                        "scope": "telegram",
                        "status": "partial",
                        "detail": "Outbound operator-topic logging is configured and inbound commands are implemented, but telegram.bot_token is still missing so topic-side commands cannot be received yet.",
                        "action": f"Add telegram.bot_token or export {current.telegram.bot_token_env} and ensure the bot can receive messages in the operator topic chat before relying on /status, /pause, /resume, or /close from Telegram.",
                    }
                )
        if current.trading.paused:
            gaps.append(
                {
                    "id": "trading_paused",
                    "scope": "runtime",
                    "status": "attention",
                    "detail": f"Trading is currently paused: {self._pause_reason or 'manual or automatic pause is active.'}",
                    "action": "Fix the underlying issue, then resume trading from Web or the runtime API.",
                }
            )
        return gaps

    def verification_report(self) -> dict[str, Any]:
        config = self.config_manager.get()
        checks = self.readiness_checks()
        usage_paths = self.usage_paths()

        return {
            "status": self._verification_status(checks),
            "checks": checks,
            "run_paths": usage_paths,
            "capabilities": self.capability_summary(config),
            "activation_summary": self.activation_summary(config),
            "remaining_gaps": self.remaining_gaps(config),
            "next_steps": self._verification_next_steps(config, usage_paths),
            "snapshot": self.snapshot(),
        }

    def public_verification_report(self) -> dict[str, Any]:
        report = self.verification_report()
        report["snapshot"] = self.public_snapshot()
        report["secret_status"] = self.secret_status()
        report["wiring"] = self.wiring_summary()
        return redact_sensitive_data(report)

    def process_operator_message(self, message: NormalizedMessage) -> None:
        if message.event_type != "new":
            return
        result = self.run_operator_command(
            message.content_text(),
            source="telegram",
            message=message,
        )
        if result.get("handled") and result.get("push_reply") and result.get("reply"):
            self._send_topic_update(str(result["reply"]))

    def run_operator_command(
        self,
        text: str,
        source: str = "manual",
        message: NormalizedMessage | None = None,
    ) -> dict[str, Any]:
        command_text = str(text or "").strip()
        command, args, slash_invoked = _parse_operator_command(command_text)
        if not command:
            return {
                "handled": False,
                "status": "ignored",
                "command": "",
                "reply": "",
                "push_reply": False,
                "source": source,
            }

        context: dict[str, Any] = {"source": source, "command": command, "args": args}
        if message:
            context.update({"chat_id": message.chat_id, "message_id": message.message_id, "version": message.version})

        try:
            if command == "help":
                reply = self._operator_help_text()
                result = {"status": "ok", "reply": reply, "push_reply": True}
            elif command == "status":
                result = {"status": "ok", "reply": self._operator_status_text(), "push_reply": True}
            elif command == "readiness":
                result = {"status": "ok", "reply": self._operator_readiness_text(), "push_reply": True}
            elif command == "paths":
                result = {"status": "ok", "reply": self._operator_paths_text(), "push_reply": True}
            elif command == "channels":
                result = {"status": "ok", "reply": self._operator_channels_text(), "push_reply": True}
            elif command == "signals":
                limit = min(max(int(args[0]), 1), 10) if args and args[0].isdigit() else 5
                result = {"status": "ok", "reply": self._operator_signals_text(limit), "push_reply": True, "limit": limit}
            elif command == "risk":
                result = {"status": "ok", "reply": self._operator_risk_text(), "push_reply": True}
            elif command == "positions":
                result = {"status": "ok", "reply": self._operator_positions_text(), "push_reply": True}
            elif command == "orders":
                limit = min(max(int(args[0]), 1), 10) if args and args[0].isdigit() else 5
                result = {"status": "ok", "reply": self._operator_orders_text(limit), "push_reply": True, "limit": limit}
            elif command == "pause":
                reason = " ".join(args).strip() or "Manual pause from operator topic"
                self.pause_trading(reason)
                result = {"status": "ok", "reply": f"[operator] paused: {reason}", "push_reply": False}
            elif command == "resume":
                reason = " ".join(args).strip() or "Manual resume from operator topic"
                self.resume_trading(reason)
                result = {"status": "ok", "reply": f"[operator] resumed: {reason}", "push_reply": False}
            elif command == "reconcile":
                summary = self.reconcile_now()
                result = {
                    "status": summary["status"],
                    "reply": f"[operator] reconcile {summary['status']}: {summary['detail']}",
                    "push_reply": True,
                    "summary": summary,
                }
            elif command == "close":
                symbol = None if not args or args[0].lower() == "all" else args[0].upper()
                closed = self.close_positions(symbol)
                symbols = ", ".join(item["symbol"] for item in closed["closed"])
                result = {
                    "status": "ok",
                    "reply": f"[operator] closed: {symbols}",
                    "push_reply": False,
                    "closed": closed,
                }
            elif command == "topic_test":
                topic_result = self.send_topic_test()
                detail = topic_result.get("reason") or topic_result.get("stderr") or topic_result.get("target") or ""
                result = {
                    "status": str(topic_result.get("status") or ("sent" if topic_result.get("sent") else "failed")),
                    "reply": f"[operator] topic-test: {detail}".strip(),
                    "push_reply": False,
                    "topic_result": topic_result,
                }
            else:
                raise ValueError(f"Unknown operator command: {command}")
        except (RuntimeError, ValueError) as exc:
            reply = f"[operator:error] {exc}"
            result = {"status": "error", "reply": reply, "push_reply": True}
            if slash_invoked and command == "unknown":
                result["reply"] = f"{reply}\n{self._operator_help_text()}"
        result.update({"handled": True, "command": command, "args": args, "source": source})
        self.log("info", "operator", "Operator command handled", {**context, "status": result["status"]}, audit=True)
        return result

    def _operator_help_text(self) -> str:
        return (
            "[operator] commands\n"
            "/help\n"
            "/status\n"
            "/readiness\n"
            "/paths\n"
            "/channels\n"
            "/signals [limit]\n"
            "/risk\n"
            "/positions\n"
            "/orders [limit]\n"
            "/pause [reason]\n"
            "/resume [reason]\n"
            "/reconcile\n"
            "/close [SYMBOL|all]\n"
            "/topic-test"
        )

    def _operator_status_text(self) -> str:
        snapshot = self.public_snapshot()
        gaps = [item["id"] for item in snapshot.get("remaining_gaps", [])[:4]]
        return "\n".join(
            [
                f"[status] mode={snapshot['config']['trading']['mode']}/{snapshot['config']['trading']['execution_mode']}",
                f"paused={snapshot['operator_state']['paused']} okx={snapshot['wiring']['okx_execution_path']} telegram={snapshot['wiring']['telegram_watch_mode']}",
                f"positions={snapshot['dashboard']['positions_count']} orders={len(snapshot['orders'])} topic={snapshot['wiring']['topic_target'] or 'n/a'}",
                f"last_reconcile={snapshot['operator_state']['last_reconcile']['status']}",
                f"gaps={', '.join(gaps) if gaps else 'none'}",
            ]
        )

    def _operator_readiness_text(self) -> str:
        report = self.public_verification_report()
        notable = [item for item in report["checks"] if item["status"] != "pass"][:4]
        lines = [f"[readiness] status={report['status']}"]
        for item in notable:
            lines.append(f"{item['name']}={item['status']} {item['detail']}")
        if len(lines) == 1:
            lines.append("all checks passing")
        return "\n".join(lines)

    def _operator_paths_text(self) -> str:
        paths = self.usage_paths()
        return "\n".join(
            [
                f"[paths] web={paths['web_login']}",
                f"config={paths['config_path']}",
                f"runtime={paths['runtime_state_dir']}",
                f"sqlite={paths['sqlite_path']}",
                f"okx_actions={','.join(paths['configured_okx_supported_actions'])}",
                f"topic={paths['topic_target_link'] or paths['topic_target'] or 'n/a'}",
                f"topic_ingress={paths['operator_command_ingress']}",
            ]
        )

    def _operator_channels_text(self) -> str:
        channels = self.config_manager.get().telegram.channels
        if not channels:
            return "[channels] none"
        lines = [f"[channels] total={len(channels)}"]
        for channel in channels[:8]:
            target = channel.chat_id or channel.channel_username or "n/a"
            status = "enabled" if channel.enabled else "disabled"
            lines.append(
                f"{channel.id} {status} {channel.source_type} target={target} reconcile={channel.reconcile_interval_seconds}s"
            )
        return "\n".join(lines)

    def _operator_signals_text(self, limit: int) -> str:
        messages = self.snapshot()["messages"][:limit]
        if not messages:
            return "[signals] none"
        lines = [f"[signals] latest={len(messages)}"]
        for item in messages:
            payload = item.get("payload", {})
            text = str(payload.get("text") or payload.get("caption") or "").strip().replace("\n", " ")
            if len(text) > 48:
                text = f"{text[:45]}..."
            lines.append(
                f"{item['chat_id']}:{item['message_id']} v{item['version']} {item['event_type']} {item['status']} {text or '[no text]'}"
            )
        return "\n".join(lines)

    def _operator_risk_text(self) -> str:
        snapshot = self.public_snapshot()
        trading = snapshot["config"]["trading"]
        health = snapshot["health"]
        gaps = [item["id"] for item in snapshot.get("remaining_gaps", []) if item["scope"] in {"runtime", "okx"}][:4]
        return "\n".join(
            [
                f"[risk] paused={snapshot['operator_state']['paused']} close_only={trading['readonly_close_only']}",
                f"leverage={trading['default_leverage']} margin={trading['margin_mode']} mode={trading['mode']}/{trading['execution_mode']}",
                f"global_tp_sl={trading['global_tp_sl_enabled']} tp={trading['global_take_profit_ratio']} sl={trading['global_stop_loss_ratio']}",
                f"okx={health['okx_rest']['status']} topic={health['topic_logger']['status']} gaps={', '.join(gaps) if gaps else 'none'}",
            ]
        )

    def _operator_positions_text(self) -> str:
        positions = self.snapshot()["positions"]
        open_positions = [
            item for item in positions
            if float(item.get("payload", {}).get("qty", 0.0)) > 0
            and item.get("payload", {}).get("side") in {"long", "short"}
        ]
        if not open_positions:
            return "[positions] none"
        lines = ["[positions]"]
        for item in open_positions[:5]:
            payload = item["payload"]
            lines.append(f"{item['symbol']} {payload['side']} qty={payload['qty']} lev={payload['leverage']}")
        return "\n".join(lines)

    def _operator_orders_text(self, limit: int) -> str:
        orders = self.snapshot()["orders"][:limit]
        if not orders:
            return "[orders] none"
        lines = [f"[orders] latest={len(orders)}"]
        for item in orders:
            lines.append(f"{item['symbol']} {item['action']} {item['status']} {item['mode']}")
        return "\n".join(lines)

    def update_config(self, patch: dict[str, Any]) -> AppConfig:
        def mutator(config: AppConfig) -> None:
            replace_config(config, merge_config_patch(config, patch))

        updated = self.config_manager.update(mutator)
        self.on_config_change(updated)
        self.log("info", "config", "Configuration updated", patch, audit=True)
        self._sync_runtime_artifacts()
        return updated

    def upsert_channel(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_channel_payload(payload)

        def mutator(config: AppConfig) -> None:
            channels = config.to_dict()["telegram"]["channels"]
            replaced = False
            for index, channel in enumerate(channels):
                if channel["id"] == normalized["id"]:
                    channels[index] = normalized
                    replaced = True
                    break
            if not replaced:
                channels.append(normalized)
            channels.sort(key=lambda item: (0 if item.get("enabled", True) else 1, item["id"]))
            replace_config(config, merge_config_patch(config, {"telegram": {"channels": channels}}))

        updated = self.config_manager.update(mutator)
        self.on_config_change(updated)
        self.log("info", "config", "Channel upserted", {"channel_id": normalized["id"]}, audit=True)
        self._sync_runtime_artifacts()
        return normalized

    def set_channel_enabled(self, channel_id: str, enabled: bool) -> dict[str, Any]:
        updated_channel: dict[str, Any] | None = None

        def mutator(config: AppConfig) -> None:
            nonlocal updated_channel
            channels = config.to_dict()["telegram"]["channels"]
            found = False
            for index, channel in enumerate(channels):
                if channel["id"] != channel_id:
                    continue
                found = True
                channel = dict(channel)
                channel["enabled"] = enabled
                channels[index] = channel
                updated_channel = channel
                break
            if not found:
                raise ValueError(f"Unknown channel id: {channel_id}")
            replace_config(config, merge_config_patch(config, {"telegram": {"channels": channels}}))

        updated = self.config_manager.update(mutator)
        self.on_config_change(updated)
        self.log("info", "config", "Channel state updated", {"channel_id": channel_id, "enabled": enabled}, audit=True)
        self._sync_runtime_artifacts()
        return updated_channel or {}

    def remove_channel(self, channel_id: str) -> None:
        def mutator(config: AppConfig) -> None:
            channels = config.to_dict()["telegram"]["channels"]
            next_channels = [channel for channel in channels if channel["id"] != channel_id]
            if len(next_channels) == len(channels):
                raise ValueError(f"Unknown channel id: {channel_id}")
            replace_config(config, merge_config_patch(config, {"telegram": {"channels": next_channels}}))

        updated = self.config_manager.update(mutator)
        self.on_config_change(updated)
        self.log("info", "config", "Channel removed", {"channel_id": channel_id}, audit=True)
        self._sync_runtime_artifacts()

    def resume_trading(self, reason: str = "Manual resume from operator") -> None:
        config = self.config_manager.get()
        if not config.trading.paused:
            self._last_resume_reason = reason
            self._last_resume_at = utc_now()
            self._refresh_trading_runtime_health(config, detail="Trading is already running")
            self._sync_runtime_artifacts()
            return
        self.update_config({"trading": {"paused": False}})
        self._last_resume_reason = reason
        self._last_resume_at = utc_now()
        self._pause_reason = ""
        self._paused_at = ""
        self._refresh_trading_runtime_health(self.config_manager.get(), detail=reason)
        self.log("info", "risk", reason, {"resumed": True}, audit=True)
        self._send_topic_update(f"[resume] {reason}")
        self._sync_runtime_artifacts()

    def reconcile_now(self) -> dict[str, Any]:
        summary = self._run_reconcile_cycle(manual=True)
        self.log("info", "recovery", "Manual reconciliation requested", summary, audit=True)
        self._sync_runtime_artifacts()
        return summary

    def send_topic_test(self) -> dict[str, Any]:
        target = resolve_topic_target(self.config_manager.get())
        if not target:
            raise ValueError("telegram.report_topic or telegram.operator_target is not configured")
        text = f"[topic-test] runtime={self._health.get('trading_runtime', {}).get('status', 'unknown')} at {utc_now()}"
        result = self.topic_logger.send(text)
        result["target_link"] = topic_target_to_link(target)
        if result.get("sent"):
            self._set_health("topic_logger", "ok", f"Topic smoke succeeded for {result.get('target', target)}")
        elif result.get("status") == "disabled":
            self._set_health("topic_logger", "disabled", str(result.get("reason") or "Topic delivery disabled"))
        else:
            self._set_health("topic_logger", "error", str(result.get("reason") or result.get("stderr") or "Topic smoke failed"))
        self.log("info", "telegram", "Topic smoke test executed", result, audit=True)
        self._sync_runtime_artifacts()
        return result

    def close_positions(self, symbol: str | None = None) -> dict[str, Any]:
        positions = [
            item for item in self.okx.positions()
            if float(item.get("qty", 0.0)) > 0 and item.get("side") in {"long", "short"}
        ]
        if symbol:
            positions = [item for item in positions if item.get("symbol") == symbol]
        if not positions:
            raise ValueError("No matching open position to close")

        closed: list[dict[str, Any]] = []
        for position in positions:
            side = "sell" if position["side"] == "long" else "buy"
            intent = TradingIntent(
                executable=True,
                action="close_all",
                symbol=str(position["symbol"]),
                market_type="swap" if "-SWAP" in str(position["symbol"]) else "futures",
                side=side,
                entry_type="market",
                size_mode="position_qty",
                size_value=float(position["qty"]),
                leverage=int(position.get("leverage", self.config_manager.get().trading.default_leverage)),
                margin_mode=str(position.get("margin_mode", self.config_manager.get().trading.margin_mode)),
                risk_level="manual",
                require_manual_confirmation=False,
                confidence=1.0,
                reason="Manual close request from Web UI.",
                raw={"manual": True},
            )
            config = self.config_manager.get()
            if self._is_observe_only(config):
                status = "observed"
                payload = {"environment": "observe_only", "action": "close_all", "symbol": intent.symbol}
                result_order_id = f"observe-close-{int(time.time())}"
            else:
                try:
                    force_simulated_close = str(position.get("source") or "simulated_demo") != "local_expected"
                    result = self.okx.execute(intent, force_simulated=force_simulated_close)
                except Exception as exc:
                    self.pause_trading("Manual close failed and trading was automatically paused")
                    self._set_health("okx_rest", "error", str(exc))
                    raise RuntimeError(str(exc)) from exc
                status = result.status
                payload = result.payload
                result_order_id = result.exchange_order_id
                if result.position_snapshot:
                    self.storage.save_position_snapshot(intent.symbol, result.position_snapshot)
            order_key = hashlib.sha256(
                json.dumps({"manual_close": intent.symbol, "ts": utc_now()}, sort_keys=True).encode("utf-8")
            ).hexdigest()
            self.storage.save_order(order_key, intent, config.trading.mode, status, payload, result_order_id)
            close_path = (
                (payload.get("execution_path") or payload.get("environment") or "unknown")
                if isinstance(payload, dict)
                else ("observe_only" if self._is_observe_only(config) else "unknown")
            )
            self.log(
                "info",
                "execution",
                "Manual close requested",
                {"symbol": intent.symbol, "status": status, "execution_path": close_path},
                audit=True,
            )
            self._send_topic_update(f"[manual-close:{config.trading.mode}] {intent.symbol} -> {status} ({close_path})")
            closed.append({"symbol": intent.symbol, "status": status, "order_id": result_order_id})
        self._sync_runtime_artifacts()
        return {"closed": closed}

    def reset_local_runtime_state(self) -> dict[str, Any]:
        config = self.config_manager.get()
        self.storage.reset_runtime_state()
        self.event_stream.clear()
        self.telegram.reset_runtime_state()
        self.okx.reset_local_state()
        self._last_reconcile = {
            "status": "idle",
            "detail": "Reconciliation has not run yet",
            "retried_incomplete": 0,
            "replayed_messages": 0,
            "updated_at": utc_now(),
        }
        self._restore_execution_state()
        detail = (
            "Cleared local runtime database, logs, sessions, and locally tracked positions. "
            "This does not cancel exchange orders or flatten any external OKX demo position."
        )
        self.log(
            "info",
            "system",
            "Local runtime state reset",
            {
                "detail": detail,
                "okx_execution_path": self.wiring_summary(config)["okx_execution_path"],
            },
            audit=True,
        )
        self._sync_runtime_artifacts()
        return {
            "status": "ok",
            "detail": detail,
            "snapshot": self.public_snapshot(),
            "run_paths": self.usage_paths(),
        }

    def readiness_checks(self) -> list[dict[str, str]]:
        config = self.config_manager.get()
        config_path = self.config_manager.path.resolve()
        data_dir = Path(config.runtime.data_dir)
        sqlite_path = Path(config.runtime.sqlite_path)
        health = self.health_snapshot()
        telegram_bot_token = resolve_telegram_bot_token(config)
        checks: list[dict[str, str]] = []

        def add_check(name: str, status: str, detail: str) -> None:
            checks.append({"name": name, "status": status, "detail": detail})

        add_check("config_file", "pass" if config_path.exists() else "fail", str(config_path))
        add_check(
            "config_persistence",
            "pass" if os.access(config_path, os.W_OK) else "fail",
            f"{config_path.name} is writable" if os.access(config_path, os.W_OK) else f"{config_path.name} is not writable",
        )
        add_check("data_dir", "pass" if data_dir.is_dir() and os.access(data_dir, os.W_OK) else "fail", str(data_dir))
        add_check("sqlite", "pass" if sqlite_path.exists() and os.access(sqlite_path, os.W_OK) else "fail", str(sqlite_path))
        try:
            resolve_pin_hash(config)
        except Exception as exc:
            add_check("web_auth", "fail", str(exc))
        else:
            add_check("web_auth", "pass", "6-digit web PIN is configured")
        add_check("demo_only_guard", "pass", "live trading remains disabled")
        add_check(
            "trading_runtime",
            "warn" if config.trading.paused else "pass",
            health.get("trading_runtime", {}).get("detail", "Trading runtime state unavailable"),
        )
        okx_health = health.get("okx_rest", {})
        if okx_health.get("status") == "error":
            add_check("okx_demo", "warn", okx_health.get("detail", "Last OKX demo execution failed"))
        elif config.okx.enabled:
            reachability = self._endpoint_reachability("okx_rest_base", config.okx.rest_base, enabled=True)
            add_check(
                "okx_demo",
                "pass" if reachability["status"] == "reachable" else "warn",
                (
                    "OKX demo REST credentials are configured and endpoint reachability looks healthy"
                    if reachability["status"] == "reachable"
                    else f"OKX demo REST credentials are configured, but endpoint reachability is degraded: {reachability['detail']}"
                ),
            )
        else:
            add_check(
                "okx_demo",
                "pass",
                "simulated OKX demo engine is active",
            )
        enabled_channels = [channel for channel in config.telegram.channels if channel.enabled]
        enabled_bot_channels = [channel for channel in enabled_channels if channel.source_type == "bot_api"]
        unsupported_mtproto = [channel.id for channel in enabled_channels if channel.source_type == "mtproto"]
        delete_channels = [
            channel.id
            for channel in enabled_bot_channels
            if channel.listen_deletes
        ]
        if telegram_bot_token and enabled_bot_channels:
            add_check("telegram_watcher", "pass", f"{len(enabled_bot_channels)} enabled Telegram bot_api channel(s)")
        elif enabled_bot_channels:
            add_check("telegram_watcher", "warn", "Telegram polling is idle until bot_token is configured")
        else:
            add_check("telegram_watcher", "warn", "No enabled bot_api channels configured; add one in Web > Channels or config.telegram.channels for live ingestion. Manual demo injection still works.")
        if unsupported_mtproto:
            add_check("telegram_mtproto", "warn", f"Configured but not implemented in this build: {', '.join(unsupported_mtproto)}")
        if delete_channels:
            add_check(
                "telegram_delete_events",
                "warn",
                "Delete/revoke handling is not implemented for enabled channels requesting it: "
                + ", ".join(delete_channels),
            )
        openclaw_path = shutil.which("openclaw")
        if openclaw_path:
            add_check("openclaw_cli", "pass", openclaw_path)
        else:
            add_check("openclaw_cli", "warn", "openclaw CLI not found; heuristic parser fallback will be used")
        topic_target = resolve_topic_target(config)
        topic_health = health.get("topic_logger", {})
        if topic_target and topic_health.get("status") == "error":
            add_check("topic_logger", "warn", topic_health.get("detail", "Last topic delivery attempt failed"))
        elif topic_target and os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() == "1":
            add_check("topic_logger", "warn", "topic delivery is disabled by TG_OKX_DISABLE_TOPIC_SEND=1")
        elif topic_target and not openclaw_path:
            add_check("topic_logger", "warn", "topic target is configured but openclaw CLI is unavailable")
        elif topic_target and topic_health.get("status") == "ok":
            add_check("topic_logger", "pass", f"topic delivery verified in this runtime: {topic_target}")
        elif topic_target:
            add_check("topic_logger", "pass", f"topic delivery target configured: {topic_target}")
        else:
            add_check("topic_logger", "warn", "telegram.report_topic / operator_target is not configured")
        if topic_target and telegram_bot_token:
            add_check("operator_commands", "pass", "Operator topic commands can be received through the configured Telegram bot")
        elif topic_target:
            add_check("operator_commands", "warn", "Operator topic target is configured, but telegram.bot_token is still missing for inbound commands")
        else:
            add_check("operator_commands", "warn", "Configure telegram.operator_target or report_topic before relying on topic-side operator commands")
        wiring = self.wiring_summary(config)
        if wiring["web_restart_required"]:
            add_check(
                "web_bind",
                "warn",
                f"Config expects {config.web.host}:{config.web.port}, but the running HTTP server is still bound to {wiring['web_bind']}; restart serve to apply the new bind address",
            )
        add_check(
            "reconciliation",
            "pass" if self._last_reconcile["status"] in {"ok", "idle"} else "warn",
            self._last_reconcile["detail"],
        )
        add_check("simulated_positions", "pass", f"{len(self.okx.positions())} simulated position snapshot(s) restored")
        return checks

    def register_web_server(self, host: str, port: int) -> None:
        with self._web_bind_lock:
            self._web_bind_host = str(host)
            self._web_bind_port = int(port)
        self._sync_runtime_artifacts()

    def unregister_web_server(self) -> None:
        with self._web_bind_lock:
            self._web_bind_host = ""
            self._web_bind_port = 0
        self._sync_runtime_artifacts()

    def _active_web_bind(self) -> str:
        with self._web_bind_lock:
            if not self._web_bind_host or self._web_bind_port <= 0:
                return ""
            return f"{self._web_bind_host}:{self._web_bind_port}"

    def health_snapshot(self) -> dict[str, dict[str, str]]:
        with self._health_lock:
            return json.loads(json.dumps(self._health))

    def pause_trading(self, reason: str) -> None:
        config = self.config_manager.get()
        self._pause_reason = reason
        self._paused_at = utc_now()
        if config.trading.paused:
            self._refresh_trading_runtime_health(config, detail=reason)
            self.log("warn", "risk", reason, {"auto_paused": True})
            self._sync_runtime_artifacts()
            return
        self.update_config({"trading": {"paused": True}})
        self._refresh_trading_runtime_health(self.config_manager.get(), detail=reason)
        self.log("warn", "risk", reason, {"auto_paused": True}, audit=True)
        self._send_topic_update(f"[pause] {reason}")
        self._sync_runtime_artifacts()

    def _retry_incomplete_messages(self, limit: int = 20) -> int:
        if self.config_manager.get().trading.paused:
            return 0
        pending = self.storage.incomplete_messages(limit)
        retried = 0
        for item in pending:
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            try:
                self._run_pipeline(NormalizedMessage(**payload))
                retried += 1
            except Exception as exc:
                self.log("error", "recovery", "Retrying incomplete message failed", {"error": str(exc), "payload": payload})
        return retried

    def _run_reconcile_cycle(self, manual: bool) -> dict[str, Any]:
        config = self.config_manager.get()
        if config.trading.paused:
            summary = {
                "status": "skipped",
                "detail": "Reconciliation skipped because trading is paused",
                "retried_incomplete": 0,
                "replayed_messages": 0,
                "updated_at": utc_now(),
            }
            self._last_reconcile = summary
            self._set_health("reconciliation", "skipped", summary["detail"])
            self._sync_runtime_artifacts()
            return summary
        try:
            retried = self._retry_incomplete_messages()
            replayed = self.telegram.reconcile_once(self.process_message)
        except Exception as exc:
            summary = {
                "status": "warn",
                "detail": f"Reconciliation failed: {exc}",
                "retried_incomplete": 0,
                "replayed_messages": 0,
                "updated_at": utc_now(),
                "manual": manual,
            }
            self._last_reconcile = summary
            self._set_health("reconciliation", "warn", summary["detail"])
            self.log("error", "recovery", "Reconciliation failed", {"error": str(exc), "manual": manual})
            self._sync_runtime_artifacts()
            return summary
        summary = {
            "status": "ok",
            "detail": f"retried {retried} incomplete message(s); replayed {replayed} buffered Telegram message(s)",
            "retried_incomplete": retried,
            "replayed_messages": replayed,
            "updated_at": utc_now(),
            "manual": manual,
        }
        self._last_reconcile = summary
        self._set_health("reconciliation", "ok", summary["detail"])
        self._sync_runtime_artifacts()
        return summary

    def _apply_global_protection(self, intent: TradingIntent, config: AppConfig) -> TradingIntent:
        if not config.trading.global_tp_sl_enabled:
            return intent
        if intent.action not in {
            "open_long",
            "open_short",
            "add_long",
            "add_short",
            "reverse_to_long",
            "reverse_to_short",
        }:
            return intent
        if intent.tp or intent.sl:
            return intent
        tp = []
        sl = None
        if config.trading.global_take_profit_ratio > 0:
            tp.append({
                "mode": "global_ratio",
                "ratio": float(config.trading.global_take_profit_ratio),
            })
        if config.trading.global_stop_loss_ratio > 0:
            sl = {
                "mode": "global_ratio",
                "ratio": float(config.trading.global_stop_loss_ratio),
            }
        if not tp and not sl:
            return intent
        enriched = intent.to_dict()
        enriched["tp"] = tp
        enriched["sl"] = sl
        raw = dict(enriched.get("raw") or {})
        raw["global_tp_sl_applied"] = True
        enriched["raw"] = raw
        return TradingIntent(**enriched)

    def _topic_target_detail(self, config: AppConfig | None = None) -> str:
        target = resolve_topic_target(config or self.config_manager.get())
        return f"topic target {target}" if target else "telegram.report_topic or operator_target not configured"

    def _topic_health_state(self, config: AppConfig | None = None) -> tuple[str, str]:
        current = config or self.config_manager.get()
        target = resolve_topic_target(current)
        if not target:
            return "idle", self._topic_target_detail(current)
        if os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() == "1":
            return "disabled", "Topic delivery disabled by TG_OKX_DISABLE_TOPIC_SEND=1"
        return "configured", self._topic_target_detail(current)

    def _is_observe_only(self, config: AppConfig) -> bool:
        return config.trading.mode == "observe" or config.trading.execution_mode == "observe"

    def _build_initial_health(self, config: AppConfig) -> dict[str, dict[str, str]]:
        return {
            "telegram_watcher": {
                "status": "idle" if not resolve_telegram_bot_token(config) else "configured",
                "detail": "telegram.bot_token not configured" if not resolve_telegram_bot_token(config) else "Watcher ready",
                "updated_at": utc_now(),
            },
            "okx_rest": {
                "status": "simulated" if not config.okx.enabled else "configured",
                "detail": "Simulated OKX demo engine" if not config.okx.enabled else "OKX demo REST path configured",
                "updated_at": utc_now(),
            },
            "okx_ws": {
                "status": "not_connected",
                "detail": "Private WebSocket is not implemented in this build",
                "updated_at": utc_now(),
            },
            "openclaw_agent": {
                "status": config.ai.provider,
                "detail": f"provider={config.ai.provider}",
                "updated_at": utc_now(),
            },
            "database": {
                "status": "ok",
                "detail": "SQLite storage is ready",
                "updated_at": utc_now(),
            },
            "trading_runtime": {
                "status": "paused" if config.trading.paused else ("observe" if self._is_observe_only(config) else "running"),
                "detail": "Trading state initialized",
                "updated_at": utc_now(),
            },
            "reconciliation": {
                "status": "idle",
                "detail": "Reconciliation has not run yet",
                "updated_at": utc_now(),
            },
            "topic_logger": {
                "status": self._topic_health_state(config)[0],
                "detail": self._topic_health_state(config)[1],
                "updated_at": utc_now(),
            },
        }

    def _update_telegram_health(self, status: str, detail: str) -> None:
        self._set_health("telegram_watcher", status, detail)

    def _set_health(self, component: str, status: str, detail: str) -> None:
        with self._health_lock:
            self._health[component] = {
                "status": status,
                "detail": detail,
                "updated_at": utc_now(),
            }

    def _endpoint_reachability(
        self,
        cache_key: str,
        url: str,
        *,
        enabled: bool,
        ttl_seconds: int = 60,
    ) -> dict[str, str]:
        if not enabled:
            return {"status": "disabled", "detail": "endpoint is not enabled"}
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if not hostname:
            return {"status": "invalid", "detail": f"invalid endpoint: {url}"}
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme in {"https", "wss"} else 80
        now = time.time()
        with self._reachability_lock:
            cached = self._reachability_cache.get(cache_key)
            if cached and (now - float(cached.get("checked_at", 0))) < ttl_seconds:
                return {
                    "status": str(cached["status"]),
                    "detail": str(cached["detail"]),
                }
        try:
            socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            result = {
                "status": "reachable",
                "detail": f"{hostname}:{port} resolved successfully",
            }
        except OSError as exc:
            result = {
                "status": "unreachable",
                "detail": f"{hostname}:{port} resolution failed: {exc}",
            }
        with self._reachability_lock:
            self._reachability_cache[cache_key] = {
                "checked_at": now,
                **result,
            }
        return result

    def _verification_status(self, checks: list[dict[str, str]]) -> str:
        if any(item["status"] == "fail" for item in checks):
            return "error"
        if any(item["status"] == "warn" for item in checks):
            return "warn"
        return "ok"

    def _verification_next_steps(
        self,
        config: AppConfig | None = None,
        usage_paths: dict[str, Any] | None = None,
    ) -> list[str]:
        current = config or self.config_manager.get()
        paths = usage_paths or self.usage_paths()
        telegram_bot_token = resolve_telegram_bot_token(current)
        topic_target = resolve_topic_target(current)
        topic_send_disabled = os.environ.get("TG_OKX_DISABLE_TOPIC_SEND", "").strip() == "1"
        openclaw_available = bool(shutil.which("openclaw"))
        topic_health_status = self.health_snapshot().get("topic_logger", {}).get("status")
        enabled_channels = [channel for channel in current.telegram.channels if channel.enabled]
        enabled_bot_channels = [channel for channel in enabled_channels if channel.source_type == "bot_api"]

        next_steps: list[str] = []
        next_steps.append(f"Verify local readiness with `{paths['verify_command']}`.")
        next_steps.append(f"Start the web console with `{paths['serve_command']}` and open {paths['web_login']}.")
        next_steps.append(f"Dry-run the pipeline with `{paths['inject_demo_signal_command']}`.")
        if current.okx.enabled:
            next_steps.append(
                "Configured OKX execution uses the OKX demo REST path only. Manual `inject-message` stays simulated by default; add `--real-okx-demo` when you intentionally want a credentialed OKX demo order."
            )
            unsupported_actions = self.wiring_summary(current)["configured_okx_unsupported_actions"]
            if unsupported_actions:
                next_steps.append(
                    "Configured OKX demo REST coverage is still partial. Keep "
                    + ", ".join(unsupported_actions)
                    + " on the simulated path until REST support is extended."
                )
        current_secret_sources = secret_sources(current)
        if current_secret_sources["telegram_bot_token"] == "config" or current_secret_sources["okx_demo_credentials"] == "config":
            next_steps.append(
                f"Inline Telegram/OKX secrets are still stored in the config file. Prefer `{paths['externalize_secrets_command']}` so the local `.env` carries secrets while the checked config stays redacted."
            )
        if paths["web_restart_required"]:
            next_steps.append(
                f"Web config changed to {paths['configured_web_login']} but the running server is still bound to {paths['web_login']}; restart `serve` to apply the new bind address."
            )
        if not telegram_bot_token:
            next_steps.append(
                f"Add `telegram.bot_token` or export `{current.telegram.bot_token_env}` before expecting live Telegram polling."
            )
        if not enabled_bot_channels:
            next_steps.append(
                "Add at least one enabled `bot_api` Telegram channel entry before expecting live signal ingestion. Manual Web/CLI demo injection already works without a Telegram source channel. The Web channel form accepts chat ids, @usernames, `https://t.me/<username>`, and `https://t.me/c/<chat>/<message>` links."
            )
        if current.trading.paused:
            next_steps.append("Trading is paused. Resume from Web or call the runtime resume action after fixing the underlying issue.")
        if not topic_target:
            next_steps.append(
                "Optional: set `telegram.report_topic` or `telegram.operator_target` to forward runtime logs into the operator topic. `https://t.me/c/<chat>/<topic>` links are accepted."
            )
        elif topic_send_disabled:
            next_steps.append("Topic delivery is currently disabled by `TG_OKX_DISABLE_TOPIC_SEND=1`; unset it before expecting operator-topic smoke logs.")
        elif not openclaw_available:
            next_steps.append("Install or expose the `openclaw` CLI before expecting operator-topic smoke logs or runtime broadcasts.")
        elif topic_health_status == "error":
            next_steps.append("Last operator-topic delivery failed; fix Telegram delivery/network access and rerun the topic smoke action.")
        elif topic_health_status == "ok":
            next_steps.append("Outbound operator-topic delivery has already been verified in this runtime; rerun the smoke after changing topic wiring.")
        else:
            next_steps.append(
                f"Verify outbound operator-topic delivery with `{paths['topic_test_command']}` or the Web Topic Smoke action."
            )
        if topic_target and telegram_bot_token:
            next_steps.append(
                "Operator topic commands are available through the configured Telegram bot. Supported commands include `/status`, `/channels`, `/signals`, `/risk`, `/positions`, `/pause`, `/resume`, `/reconcile`, `/close`, and `/topic-test`."
            )
        elif topic_target:
            next_steps.append(
                f"Operator topic outbound delivery is wired. Add `telegram.bot_token` or export `{current.telegram.bot_token_env}` and allow the bot to receive topic messages before relying on inbound operator commands such as `/status` or `/pause`."
            )
        if any(channel.source_type == "mtproto" and channel.enabled for channel in current.telegram.channels):
            next_steps.append("Enabled mtproto channels are stored but not consumed in this dependency-light build; use bot_api for active watching.")
        return next_steps


def _normalize_channel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    channel_username = normalize_channel_username(payload.get("channel_username", ""))
    chat_id = normalize_chat_id(payload.get("chat_id", ""))
    base_id = (
        str(payload.get("id", "") or "").strip()
        or channel_username
        or chat_id.replace("-100", "chan-")
        or str(payload.get("name", "") or "").strip()
    )
    channel_id = _slugify_channel_id(base_id)
    if not channel_id:
        raise ValueError("Channel id could not be derived; provide id, username, chat_id, or name")
    return {
        "id": channel_id,
        "name": str(payload.get("name", "") or channel_username or chat_id or channel_id).strip(),
        "source_type": str(payload.get("source_type", "bot_api") or "bot_api").strip(),
        "chat_id": chat_id,
        "channel_username": channel_username,
        "enabled": bool(payload.get("enabled", True)),
        "priority": int(payload.get("priority", 100)),
        "parse_profile_id": str(payload.get("parse_profile_id", "default") or "default"),
        "strategy_profile_id": str(payload.get("strategy_profile_id", "default") or "default"),
        "risk_profile_id": str(payload.get("risk_profile_id", "default") or "default"),
        "paper_trading_enabled": bool(payload.get("paper_trading_enabled", True)),
        "live_trading_enabled": False,
        "listen_new_messages": bool(payload.get("listen_new_messages", True)),
        "listen_edits": bool(payload.get("listen_edits", True)),
        "listen_deletes": bool(payload.get("listen_deletes", False)),
        "reconcile_interval_seconds": int(payload.get("reconcile_interval_seconds", 30)),
        "dedup_window_seconds": int(payload.get("dedup_window_seconds", 3600)),
        "notes": str(payload.get("notes", "") or "").strip(),
    }


def _slugify_channel_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-_").lower()
    return slug[:64]


def _intent_protection_summary(intent: TradingIntent) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if intent.tp:
        payload["tp"] = intent.tp
    if intent.sl:
        payload["sl"] = intent.sl
    if intent.trailing:
        payload["trailing"] = intent.trailing
    return payload


def _parse_operator_command(text: str) -> tuple[str, list[str], bool]:
    raw = str(text or "").strip()
    if not raw:
        return "", [], False
    parts = raw.split()
    head = parts[0].strip()
    slash_invoked = head.startswith("/")
    normalized = head.lstrip("/").split("@", 1)[0].strip().lower().replace("_", "-")
    aliases = {
        "help": "help",
        "status": "status",
        "readiness": "readiness",
        "paths": "paths",
        "channels": "channels",
        "signals": "signals",
        "risk": "risk",
        "positions": "positions",
        "orders": "orders",
        "pause": "pause",
        "resume": "resume",
        "reconcile": "reconcile",
        "close": "close",
        "topic-test": "topic_test",
        "topictest": "topic_test",
    }
    command = aliases.get(normalized, "")
    if command:
        return command, parts[1:], slash_invoked
    if slash_invoked:
        return "unknown", parts[1:], True
    return "", [], False
