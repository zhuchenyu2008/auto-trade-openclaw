from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
import urllib.parse
import urllib.request
from typing import Any, Callable

from .config import AppConfig, ChannelConfig, resolve_telegram_bot_token, resolve_topic_target, topic_target_parts
from .models import NormalizedMessage, utc_now


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
        self._public_web_state: dict[tuple[str, int], dict[str, Any]] = {}

    def run_forever(self, callback: Callable[[NormalizedMessage], None]) -> None:
        self.stop_event.clear()
        while not self.stop_event.is_set():
            config = self.config_getter()
            token = resolve_telegram_bot_token(config)
            enabled_bot_channels = [
                channel
                for channel in config.telegram.channels
                if channel.enabled and channel.source_type == "bot_api"
            ]
            enabled_public_web_channels = [
                channel
                for channel in config.telegram.channels
                if channel.enabled and channel.source_type == "public_web"
            ]
            if not token and not enabled_public_web_channels:
                self._publish_health("idle", "telegram.bot_token not configured")
                time.sleep(config.telegram.poll_interval_seconds)
                continue
            try:
                if token:
                    updates = self._get_updates(token, self.offset)
                    for update in updates:
                        self._process_update(update, callback, config)
                if enabled_public_web_channels:
                    self._poll_public_web_channels(enabled_public_web_channels, callback)
                detail_parts: list[str] = []
                if enabled_bot_channels:
                    if token:
                        detail_parts.append(f"{len(enabled_bot_channels)} enabled bot_api channel(s)")
                    else:
                        detail_parts.append(
                            f"{len(enabled_bot_channels)} enabled bot_api channel(s), waiting for bot token"
                        )
                if enabled_public_web_channels:
                    detail_parts.append(f"{len(enabled_public_web_channels)} enabled public_web channel(s)")
                self._publish_health(
                    "connected" if enabled_bot_channels or enabled_public_web_channels else "configured",
                    ", ".join(detail_parts) if detail_parts else "No enabled Telegram channels",
                )
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
        self._public_web_state = {}

    def reconcile_once(self, callback: Callable[[NormalizedMessage], None]) -> int:
        config = self.config_getter()
        token = resolve_telegram_bot_token(config)
        replayed = 0
        if token:
            for channel in config.telegram.channels:
                if not channel.enabled or channel.source_type != "bot_api":
                    continue
                history = self._get_chat_history(token, channel)
                for item in history:
                    event_type = "edit" if item.get("edit_date") else "new"
                    callback(self._normalize_message("bot_api", event_type, item))
                    replayed += 1
        public_web_channels = [
            channel
            for channel in config.telegram.channels
            if channel.enabled and channel.source_type == "public_web"
        ]
        if public_web_channels:
            replayed += self._poll_public_web_channels(public_web_channels, callback)
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

    def _poll_public_web_channels(
        self,
        channels: list[ChannelConfig],
        callback: Callable[[NormalizedMessage], None],
    ) -> int:
        emitted = 0
        for channel in channels:
            html = self._get_public_channel_html(channel.channel_username)
            for post in parse_public_channel_html(channel.channel_username, html):
                normalized = self._normalize_public_web_post(channel, post)
                if normalized is None:
                    continue
                callback(normalized)
                emitted += 1
        return emitted

    def _normalize_public_web_post(
        self,
        channel: ChannelConfig,
        post: dict[str, Any],
    ) -> NormalizedMessage | None:
        key = (channel.id, int(post["message_id"]))
        current = NormalizedMessage.from_public_web(
            channel.channel_username,
            "new",
            post,
            version=1,
        )
        state = self._public_web_state.get(key)
        if state is None:
            self._public_web_state[key] = {
                "semantic_hash": current.semantic_hash,
                "version": 1,
            }
            if channel.listen_new_messages:
                self._remember_message(
                    channel,
                    {
                        "message_id": post["message_id"],
                        "date": _iso_to_timestamp(post["date"]),
                        "text": post["text"],
                        "caption": post["caption"],
                        "chat": {
                            "id": f"public:{channel.channel_username}",
                            "username": channel.channel_username,
                        },
                    },
                )
                return current
            return None
        if state["semantic_hash"] == current.semantic_hash:
            return None
        version = int(state["version"]) + 1
        self._public_web_state[key] = {
            "semantic_hash": current.semantic_hash,
            "version": version,
        }
        if not channel.listen_edits:
            return None
        updated = NormalizedMessage.from_public_web(
            channel.channel_username,
            "edit",
            post,
            version=version,
            edit_date=utc_now(),
        )
        self._remember_message(
            channel,
            {
                "message_id": post["message_id"],
                "date": _iso_to_timestamp(post["date"]),
                "edit_date": _iso_to_timestamp(updated.edit_date or updated.date),
                "text": post["text"],
                "caption": post["caption"],
                "chat": {
                    "id": f"public:{channel.channel_username}",
                    "username": channel.channel_username,
                },
            },
        )
        return updated

    def _get_public_channel_html(self, channel_username: str) -> str:
        normalized_username = str(channel_username or "").strip().lstrip("@")
        url = f"https://t.me/s/{urllib.parse.quote(normalized_username)}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; tg-okx-auto-trade/1.0)",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8")


class _PublicChannelHTMLParser(HTMLParser):
    def __init__(self, default_channel_username: str):
        super().__init__(convert_charrefs=True)
        self.default_channel_username = default_channel_username
        self.messages: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._message_depth = 0
        self._capture_text_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if self._current is None and tag == "div" and "tgme_widget_message" in classes and attributes.get("data-post"):
            channel_username, _, raw_post_id = attributes["data-post"].partition("/")
            if not raw_post_id.isdigit():
                return
            self._current = {
                "channel_username": (channel_username or self.default_channel_username).strip().lstrip("@").lower(),
                "message_id": int(raw_post_id),
                "date": "",
                "text": "",
                "caption": "",
            }
            self._message_depth = 1
            return
        if self._current is None:
            return
        if tag == "br":
            if self._capture_text_depth > 0:
                self._text_parts.append("\n")
            return
        self._message_depth += 1
        if tag == "div" and "tgme_widget_message_text" in classes and self._capture_text_depth == 0:
            self._capture_text_depth = 1
            self._text_parts = []
            return
        if self._capture_text_depth > 0:
            self._capture_text_depth += 1
        if tag == "time" and attributes.get("datetime"):
            self._current["date"] = _normalize_public_datetime(attributes["datetime"])

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if self._capture_text_depth > 0:
            self._capture_text_depth -= 1
            if self._capture_text_depth == 0:
                text = _clean_public_text("".join(self._text_parts))
                self._current["text"] = text
                self._text_parts = []
        self._message_depth -= 1
        if self._message_depth == 0:
            if self._current.get("date") and (self._current.get("text") or self._current.get("caption")):
                self.messages.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._capture_text_depth > 0:
            self._text_parts.append(data)


def parse_public_channel_html(channel_username: str, html: str) -> list[dict[str, Any]]:
    parser = _PublicChannelHTMLParser(channel_username)
    parser.feed(html)
    parser.close()
    return sorted(parser.messages, key=lambda item: int(item["message_id"]))


def _normalize_public_datetime(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _clean_public_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\xa0", " ").splitlines()]
    return "\n".join(lines).strip()


def _iso_to_timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
