from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class NormalizedMessage:
    source: str
    adapter: str
    chat_id: str
    message_id: int
    event_type: str
    version: int
    date: str
    edit_date: str | None
    text: str
    caption: str
    media: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    reply_to: dict[str, Any] | None = None
    forward_from: dict[str, Any] | None = None
    raw_hash: str = ""
    semantic_hash: str = ""

    def content_text(self) -> str:
        return (self.text or self.caption or "").strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_telegram(
        cls,
        adapter: str,
        event_type: str,
        message: dict[str, Any],
        version: int | None = None,
    ) -> "NormalizedMessage":
        text = message.get("text", "") or ""
        caption = message.get("caption", "") or ""
        payload = {
            "source": "telegram",
            "adapter": adapter,
            "chat_id": str(message["chat"]["id"]),
            "message_id": int(message["message_id"]),
            "event_type": event_type,
            "version": version if version is not None else (2 if message.get("edit_date") else 1),
            "date": datetime.fromtimestamp(message["date"], tz=timezone.utc).replace(microsecond=0).isoformat(),
            "edit_date": datetime.fromtimestamp(message["edit_date"], tz=timezone.utc).replace(microsecond=0).isoformat() if message.get("edit_date") else None,
            "text": text,
            "caption": caption,
            "media": _extract_media(message),
            "entities": message.get("entities", []) + message.get("caption_entities", []),
            "reply_to": message.get("reply_to_message"),
            "forward_from": {
                "chat": message.get("forward_from_chat"),
                "sender_name": message.get("forward_sender_name"),
            }
            if message.get("forward_from_chat") or message.get("forward_sender_name")
            else None,
        }
        raw_body = json.dumps(message, sort_keys=True, ensure_ascii=True)
        payload["raw_hash"] = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
        payload["semantic_hash"] = hashlib.sha256(
            json.dumps(
                {
                    "chat_id": payload["chat_id"],
                    "message_id": payload["message_id"],
                    "text": text,
                    "caption": caption,
                    "media": payload["media"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return cls(**payload)


@dataclass
class TradingIntent:
    executable: bool
    action: str
    symbol: str
    market_type: str
    side: str
    entry_type: str
    size_mode: str
    size_value: float
    leverage: int
    margin_mode: str
    risk_level: str
    tp: list[dict[str, Any]] = field(default_factory=list)
    sl: dict[str, Any] | None = None
    trailing: dict[str, Any] | None = None
    require_manual_confirmation: bool = False
    confidence: float = 0.0
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def action_hash(self) -> str:
        body = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass
class RiskResult:
    approved: bool
    reason: str
    code: str
    intent: TradingIntent
    idempotency_key: str


def _extract_media(message: dict[str, Any]) -> list[dict[str, Any]]:
    media = []
    for field_name in ("photo", "video", "document", "animation"):
        if field_name in message:
            media.append({"type": field_name, "value": message[field_name]})
    return media
