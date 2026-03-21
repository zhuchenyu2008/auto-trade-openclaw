from __future__ import annotations

import hashlib
import json

from .config import AppConfig
from .models import NormalizedMessage, RiskResult, TradingIntent


VALID_ACTIONS = {
    "ignore",
    "open_long",
    "open_short",
    "add_long",
    "add_short",
    "reduce_long",
    "reduce_short",
    "close_all",
    "reverse_to_long",
    "reverse_to_short",
    "cancel_orders",
    "update_protection",
}

ACTION_SIDE_RULES = {
    "ignore": {"flat"},
    "open_long": {"buy"},
    "open_short": {"sell"},
    "add_long": {"buy"},
    "add_short": {"sell"},
    "reduce_long": {"sell"},
    "reduce_short": {"buy"},
    "close_all": {"flat"},
    "reverse_to_long": {"buy"},
    "reverse_to_short": {"sell"},
    "cancel_orders": {"flat"},
    "update_protection": {"flat"},
}

POSITIVE_SIZE_ACTIONS = {
    "open_long",
    "open_short",
    "add_long",
    "add_short",
    "reduce_long",
    "reduce_short",
    "reverse_to_long",
    "reverse_to_short",
}


class RiskEngine:
    def __init__(self, config: AppConfig):
        self.config = config

    def evaluate(self, message: NormalizedMessage, intent: TradingIntent, duplicate_exists: bool) -> RiskResult:
        idempotency_key = self._idempotency_key(message, intent)
        if duplicate_exists:
            return RiskResult(False, "Duplicate execution intent ignored", "duplicate", intent, idempotency_key)
        if self.config.trading.paused:
            return RiskResult(False, "Trading is paused", "paused", intent, idempotency_key)
        if self.config.trading.mode == "live":
            return RiskResult(False, "Live mode is disabled for this build", "live_disabled", intent, idempotency_key)
        if intent.action not in VALID_ACTIONS:
            return RiskResult(False, "Invalid action", "invalid_action", intent, idempotency_key)
        if self._is_management_message(message, intent):
            return RiskResult(
                False,
                "Management/update message recognized; skipped as a fresh trade entry",
                "management_update",
                intent,
                idempotency_key,
            )
        if not intent.executable or intent.action == "ignore":
            return RiskResult(False, "Signal ignored as non-executable", "ignored_signal", intent, idempotency_key)
        if not intent.symbol.endswith(("-SWAP", "-FUTURES")) and "-SWAP" not in intent.symbol and "-FUTURES" not in intent.symbol:
            return RiskResult(False, "Contracts only: swap/futures symbols required", "invalid_symbol", intent, idempotency_key)
        if not (1 <= intent.leverage <= 125):
            return RiskResult(False, "Invalid leverage", "invalid_leverage", intent, idempotency_key)
        if intent.market_type not in {"swap", "futures"}:
            return RiskResult(False, "Contracts only: swap/futures market_type required", "invalid_market_type", intent, idempotency_key)
        if intent.margin_mode not in {"isolated", "cross"}:
            return RiskResult(False, "Invalid margin mode", "invalid_margin_mode", intent, idempotency_key)
        if intent.action in POSITIVE_SIZE_ACTIONS and float(intent.size_value) <= 0:
            return RiskResult(False, "Executable trade size must be greater than zero", "invalid_size", intent, idempotency_key)
        expected_sides = ACTION_SIDE_RULES.get(intent.action, set())
        if expected_sides and intent.side not in expected_sides:
            return RiskResult(False, "Intent side does not match action", "invalid_side", intent, idempotency_key)
        if self.config.trading.readonly_close_only and intent.action not in {
            "close_all",
            "reduce_long",
            "reduce_short",
            "cancel_orders",
            "update_protection",
        }:
            return RiskResult(False, "Close-only mode active", "close_only", intent, idempotency_key)
        if intent.require_manual_confirmation or self.config.trading.execution_mode == "semi_automatic":
            return RiskResult(False, "Manual confirmation required", "manual_confirmation", intent, idempotency_key)
        return RiskResult(True, "Approved", "approved", intent, idempotency_key)

    def _idempotency_key(self, message: NormalizedMessage, intent: TradingIntent) -> str:
        body = {
            "channel_id": message.chat_id,
            "message_id": message.message_id,
            "version": message.version,
            "action_hash": intent.action_hash(),
            "symbol": intent.symbol,
            "side": intent.side,
            "intent_hash": intent.action_hash(),
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()

    def _is_management_message(self, message: NormalizedMessage, intent: TradingIntent) -> bool:
        if intent.action == "update_protection" and intent.executable and intent.symbol and (intent.tp or intent.sl or intent.trailing):
            return False
        if intent.action in {"close_all", "reduce_long", "reduce_short", "cancel_orders"} and intent.executable and intent.symbol:
            return False
        if intent.action == "update_protection":
            return True
        content = message.content_text().upper()
        if intent.action != "ignore":
            return False
        if any(keyword in content for keyword in ("止盈", "止损", "保本", "保护", "调保护", "减仓", "平一半", "PARTIAL", "BREAKEVEN")):
            return True
        combined_reason = " ".join(
            str(item or "")
            for item in (
                intent.reason,
                intent.raw.get("reason") if isinstance(intent.raw, dict) else "",
                intent.raw.get("provider_error") if isinstance(intent.raw, dict) else "",
            )
        ).lower()
        return any(keyword in combined_reason for keyword in ("management", "update message", "protection update"))
