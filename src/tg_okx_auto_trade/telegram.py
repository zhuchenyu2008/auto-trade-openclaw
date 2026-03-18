from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from .config import AppConfig, ChannelConfig, resolve_telegram_bot_token, resolve_topic_target, topic_target_parts
from .models import NormalizedMessage


class TelegramWatcher:
    def __init__(
        self,
        config_getter: Callable[[], AppConfig],
        logger,
        health_callback: Callable[[str, str], None] | None = None,
        operator_callback: Callable[[NormalizedMessage], None] | None = None,
    ):
        self.config_getter = config_getter
        self.logger = logger
        self.health_callback = health_callback
        self.operator_callback = operator_callback
        self.stop_event = threading.Event()
        self.offset = 0
        self._recent_messages: list[dict[str, Any]] = []
        self._message_versions: dict[tuple[str, int], dict[str, int]] = {}

    def run_forever(self, callback: Callable[[NormalizedMessage], None]) -> None:
        self.stop_event.clear()
        while not self.stop_event.is_set():
            config = self.config_getter()
            token = resolve_telegram_bot_token(config)
            if not token:
                self._publish_health("idle", "telegram.bot_token not configured")
                time.sleep(config.telegram.poll_interval_seconds)
                continue
            try:
                updates = self._get_updates(token, self.offset)
                enabled_channels = [channel for channel in config.telegram.channels if channel.enabled and channel.source_type == "bot_api"]
                self._publish_health(
                    "connected" if enabled_channels else "configured",
                    f"{len(enabled_channels)} enabled bot_api channel(s)",
                )
                for update in updates:
                    self._process_update(update, callback, config)
            except Exception as exc:
                self._publish_health("error", str(exc))
                self.logger("error", "telegram", "Telegram watcher failed", {"error": str(exc)})
            time.sleep(config.telegram.poll_interval_seconds)

    def stop(self) -> None:
        self.stop_event.set()

    def reset_runtime_state(self) -> None:
        self.offset = 0
        self._recent_messages = []
        self._message_versions = {}

    def reconcile_once(self, callback: Callable[[NormalizedMessage], None]) -> int:
        config = self.config_getter()
        token = resolve_telegram_bot_token(config)
        if not token:
            return 0
        replayed = 0
        for channel in config.telegram.channels:
            if not channel.enabled or channel.source_type != "bot_api":
                continue
            history = self._get_chat_history(token, channel)
            for item in history:
                event_type = "edit" if item.get("edit_date") else "new"
                callback(self._normalize_message("bot_api", event_type, item))
                replayed += 1
        return replayed

    def _process_update(self, update: dict[str, Any], callback: Callable[[NormalizedMessage], None], config: AppConfig | None = None) -> bool:
        current = config or self.config_getter()
        self.offset = max(self.offset, int(update["update_id"]) + 1)
        msg, event_type = self._extract_message(update)
        if not msg:
            return False
        normalized = self._normalize_message("bot_api", event_type, msg)
        if self._is_operator_message(current, msg):
            if self.operator_callback:
                self.operator_callback(normalized)
            return True
        channel = self._match_channel(current.telegram.channels, msg)
        if not channel or not channel.enabled:
            return False
        if event_type == "new" and not channel.listen_new_messages:
            return False
        if event_type == "edit" and not channel.listen_edits:
            return False
        self._remember_message(channel, msg)
        callback(normalized)
        return True

    def _normalize_message(self, adapter: str, event_type: str, message: dict[str, Any]) -> NormalizedMessage:
        return NormalizedMessage.from_telegram(
            adapter,
            event_type,
            message,
            version=self._message_version(message, event_type),
        )

    def _is_operator_message(self, config: AppConfig, message: dict[str, Any]) -> bool:
        target = resolve_topic_target(config)
        if not target:
            return False
        target_chat_id, target_thread_id = topic_target_parts(target, config.telegram.operator_thread_id)
        if not target_chat_id:
            return False
        if str(message["chat"]["id"]) != target_chat_id:
            return False
        if target_thread_id <= 0:
            return True
        return int(message.get("message_thread_id") or 0) == target_thread_id

    def _match_channel(self, channels: list[ChannelConfig], message: dict[str, Any]) -> ChannelConfig | None:
        chat_id = str(message["chat"]["id"])
        username = str(message["chat"].get("username", "") or "").lstrip("@").lower()
        for channel in channels:
            if channel.chat_id and channel.chat_id == chat_id:
                return channel
            if channel.channel_username and channel.channel_username.lstrip("@").lower() == username:
                return channel
        return None

    def _extract_message(self, update: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if "channel_post" in update:
            return update["channel_post"], "new"
        if "edited_channel_post" in update:
            return update["edited_channel_post"], "edit"
        if "message" in update:
            return update["message"], "new"
        if "edited_message" in update:
            return update["edited_message"], "edit"
        return None, ""

    def _get_updates(self, token: str, offset: int) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode({"timeout": 10, "offset": offset})
        url = f"https://api.telegram.org/bot{token}/getUpdates?{params}"
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(payload)
        return payload.get("result", [])

    def _get_chat_history(self, token: str, channel: ChannelConfig) -> list[dict[str, Any]]:
        # Bot API does not expose generic channel history reads. Reconciliation replays the recent in-process buffer.
        history = [
            item["message"]
            for item in self._recent_messages
            if item["channel_id"] == channel.id
        ]
        self.logger(
            "info",
            "telegram",
            "Bot API reconciliation fallback replayed recent buffer",
            {"channel_id": channel.id, "buffered_messages": len(history)},
        )
        return history

    def _remember_message(self, channel: ChannelConfig, message: dict[str, Any]) -> None:
        key = (channel.id, int(message["message_id"]), int(message.get("edit_date") or message["date"]))
        self._recent_messages = [item for item in self._recent_messages if item["key"] != key]
        self._recent_messages.append({"key": key, "channel_id": channel.id, "message": message})
        if len(self._recent_messages) > 200:
            self._recent_messages = self._recent_messages[-200:]

    def _message_version(self, message: dict[str, Any], event_type: str) -> int:
        key = (str(message["chat"]["id"]), int(message["message_id"]))
        marker = int(message.get("edit_date") or message["date"])
        state = self._message_versions.get(key)
        if event_type == "new":
            version = 1 if state is None else state["version"]
        elif state is None:
            version = 2
        elif state["marker"] == marker:
            version = state["version"]
        else:
            version = max(2, state["version"] + 1)
        self._message_versions[key] = {"version": version, "marker": marker}
        return version

    def _publish_health(self, status: str, detail: str) -> None:
        if self.health_callback:
            self.health_callback(status, detail)
