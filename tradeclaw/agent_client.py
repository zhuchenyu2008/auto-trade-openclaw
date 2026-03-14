from __future__ import annotations

import json
import os
import shutil
import subprocess
from textwrap import dedent
from typing import Any

from .config import AppConfig
from .models import ChannelEvent, TradeDecision
from .utils import extract_json_object


class OpenClawAgentClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.openclaw_bin = os.environ.get("OPENCLAW_BIN") or shutil.which("openclaw") or "/www/server/nodejs/v24.13.0/bin/openclaw"

    def decide(self, event: ChannelEvent, account_snapshot: dict[str, Any]) -> TradeDecision:
        safe_channel = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '-' for ch in event.channel)
        session_id = f"{self.config.openclaw.session_prefix}-{safe_channel}"
        prompt = self._build_prompt(event, account_snapshot)
        cmd = [
            self.openclaw_bin,
            "agent",
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--json",
            "--timeout",
            str(self.config.openclaw.timeout_seconds),
            "--thinking",
            self.config.openclaw.thinking,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        payloads = data.get("result", {}).get("payloads", [])
        text = payloads[0]["text"] if payloads else "{}"
        parsed = extract_json_object(text)
        return self._normalize(parsed)

    def _build_prompt(self, event: ChannelEvent, account_snapshot: dict[str, Any]) -> str:
        recent_messages = [
            {
                "post_id": m.post_id,
                "dt": m.dt,
                "text": m.text,
                "permalink": m.permalink,
            }
            for m in event.recent_messages
        ]
        payload = {
            "event": {
                "kind": event.kind,
                "channel": event.channel,
                "post_id": event.message.post_id,
                "dt": event.message.dt,
                "text": event.message.text,
                "previous_text": event.previous_text,
                "permalink": event.message.permalink,
            },
            "recent_messages": recent_messages,
            "current_okx_state": account_snapshot,
            "fixed_execution_rules": {
                "event_driven_only": True,
                "no_new_trade_decision_without_new_channel_event": True,
                "global_leverage_is_fixed_externally": self.config.okx.default_leverage,
                "position_size_bounds_pct_of_available_usdt": {
                    "min": self.config.okx.min_position_pct,
                    "max": self.config.okx.max_position_pct,
                    "default": self.config.okx.default_position_pct,
                },
                "margin_mode": self.config.okx.margin_mode,
                "position_mode": self.config.okx.position_mode,
                "allowed_instruments": self.config.okx.allowed_instruments,
                "profile": self.config.okx.profile,
            },
        }
        schema = {
            "kind": "ignore|trade",
            "intent": "ignore|open_long|open_short|close_all|close_partial|add_long|add_short|reduce_long|reduce_short|reverse_to_long|reverse_to_short|update_protection|cancel_orders",
            "symbol": "BTC-USDT-SWAP|null",
            "order_type": "market|limit",
            "limit_price": "number|null",
            "size_mode": "contracts|percent_position|percent_equity|all",
            "size_value": "number|null",
            "take_profit_trigger_price": "number|null",
            "stop_loss_trigger_price": "number|null",
            "trailing_callback_ratio": "number|null",
            "cancel_existing_orders": "boolean",
            "reason": "string",
            "confidence": "0..1",
        }
        return dedent(
            f"""
            You are Trade Claw, an event-driven OKX perpetual execution brain.

            Hard boundary:
            - You ONLY decide based on the incoming Telegram channel event.
            - You do NOT create new discretionary trade decisions when there is no new channel event.
            - Global leverage is fixed outside you. Never decide leverage.
            - Position sizing is constrained outside you: choose a size between the configured min/max percentage of available USDT, and prefer size_mode=percent_equity for open/add actions.
            - If you do not have a strong reason to vary sizing, use the configured default percentage.
            - Favor interpreting the event as management of an existing position if the new message clearly says close/reduce/add/stop-loss/take-profit/reverse.
            - If the message is noise or not actionable, return ignore.
            - Output ONE strict JSON object. No markdown, no explanation outside JSON.

            JSON schema:
            {json.dumps(schema, ensure_ascii=False)}

            Context:
            {json.dumps(payload, ensure_ascii=False, indent=2)}
            """
        ).strip()

    @staticmethod
    def _normalize(data: dict[str, Any]) -> TradeDecision:
        return TradeDecision(
            kind=str(data.get("kind") or "ignore"),
            intent=str(data.get("intent") or "ignore"),
            symbol=data.get("symbol"),
            order_type=str(data.get("order_type") or "market"),
            limit_price=float(data["limit_price"]) if data.get("limit_price") is not None else None,
            size_mode=str(data.get("size_mode") or "contracts"),
            size_value=float(data["size_value"]) if data.get("size_value") is not None else None,
            take_profit_trigger_price=float(data["take_profit_trigger_price"]) if data.get("take_profit_trigger_price") is not None else None,
            stop_loss_trigger_price=float(data["stop_loss_trigger_price"]) if data.get("stop_loss_trigger_price") is not None else None,
            trailing_callback_ratio=float(data["trailing_callback_ratio"]) if data.get("trailing_callback_ratio") is not None else None,
            cancel_existing_orders=bool(data.get("cancel_existing_orders", False)),
            reason=str(data.get("reason") or ""),
            confidence=float(data.get("confidence") or 0.0),
            raw=data,
        )
