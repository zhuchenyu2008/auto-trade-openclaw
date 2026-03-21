from __future__ import annotations

import hashlib
import json
from typing import Any

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

    def evaluate(
        self,
        message: NormalizedMessage,
        intent: TradingIntent,
        duplicate_exists: bool,
        *,
        positions: list[dict[str, Any]] | None = None,
        recent_messages: list[dict[str, Any]] | None = None,
    ) -> RiskResult:
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
        state_guard = self._evaluate_trade_state(intent, positions or [], recent_messages or [])
        if state_guard is not None:
            return RiskResult(False, state_guard["reason"], state_guard["code"], intent, idempotency_key)
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

    def _evaluate_trade_state(
        self,
        intent: TradingIntent,
        positions: list[dict[str, Any]],
        recent_messages: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        current = self._position_for_symbol(positions, intent.symbol)
        current_side = str(current.get("side") or "flat")
        current_qty = float(current.get("qty") or 0.0)
        if intent.action in {"open_long", "add_long"} and current_side == "short" and current_qty > 0:
            return {"code": "position_conflict", "reason": "当前已有空头仓位，不应直接按多头动作继续执行"}
        if intent.action in {"open_short", "add_short"} and current_side == "long" and current_qty > 0:
            return {"code": "position_conflict", "reason": "当前已有多头仓位，不应直接按空头动作继续执行"}
        if intent.action == "reduce_long":
            return self._guard_reduce_or_close(
                symbol=intent.symbol,
                current_side=current_side,
                current_qty=current_qty,
                expected_side="long",
                current=current,
                positions=positions,
                recent_messages=recent_messages,
                reason_when_flat="当前已无多头仓位，减仓消息已跳过",
            )
        if intent.action == "reduce_short":
            return self._guard_reduce_or_close(
                symbol=intent.symbol,
                current_side=current_side,
                current_qty=current_qty,
                expected_side="short",
                current=current,
                positions=positions,
                recent_messages=recent_messages,
                reason_when_flat="当前已无空头仓位，减仓消息已跳过",
            )
        if intent.action == "close_all":
            if current_qty <= 0 or current_side == "flat":
                chain_conflict = self._management_symbol_chain_conflict(intent.symbol, current, positions, recent_messages)
                if chain_conflict is not None:
                    return chain_conflict
                return {
                    "code": "already_flat",
                    "reason": "当前已空仓，平仓/出局消息已跳过",
                }
        if intent.action == "update_protection":
            if current_qty <= 0 or current_side == "flat":
                return {
                    "code": "already_flat",
                    "reason": "当前无持仓，保护更新消息已跳过",
                }
            chain_conflict = self._management_symbol_chain_conflict(intent.symbol, current, positions, recent_messages)
            if chain_conflict is not None:
                return chain_conflict
            incoming_protection = self._protection_payload(intent)
            current_protection = self._normalized_protection(current.get("protection"))
            if incoming_protection and incoming_protection == current_protection:
                return {
                    "code": "duplicate_protection",
                    "reason": "当前保护参数未变化，重复保护更新已抑制",
                }
        if intent.action == "cancel_orders":
            protection = self._normalized_protection(current.get("protection"))
            if not protection:
                return {
                    "code": "no_active_protection",
                    "reason": "当前没有可撤销的本地保护/挂单记录，撤单消息已跳过",
                }
        return None

    def _guard_reduce_or_close(
        self,
        *,
        symbol: str,
        current_side: str,
        current_qty: float,
        expected_side: str,
        current: dict[str, Any],
        positions: list[dict[str, Any]],
        recent_messages: list[dict[str, Any]],
        reason_when_flat: str,
    ) -> dict[str, str] | None:
        if current_qty <= 0 or current_side == "flat":
            return self._management_symbol_chain_conflict(symbol, current, positions, recent_messages) or {
                "code": "already_flat",
                "reason": reason_when_flat,
            }
        if current_side != expected_side:
            return self._management_symbol_chain_conflict(symbol, current, positions, recent_messages) or {
                "code": "position_side_conflict",
                "reason": "当前持仓方向与管理动作不匹配，已跳过",
            }
        return None

    def _position_for_symbol(self, positions: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
        for item in positions:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else item
            if str(payload.get("symbol") or "") == symbol:
                return {
                    "symbol": str(payload.get("symbol") or ""),
                    "side": str(payload.get("side") or "flat"),
                    "qty": float(payload.get("qty") or payload.get("pos") or payload.get("quantity") or 0.0),
                    "protection": payload.get("protection") if isinstance(payload.get("protection"), dict) else {},
                }
        return {"symbol": symbol, "side": "flat", "qty": 0.0, "protection": {}}

    def _management_symbol_chain_conflict(
        self,
        symbol: str,
        current: dict[str, Any],
        positions: list[dict[str, Any]],
        recent_messages: list[dict[str, Any]],
    ) -> dict[str, str] | None:
        recent_trade_symbol = self._recent_trade_symbol(recent_messages)
        if not recent_trade_symbol or recent_trade_symbol == symbol:
            return None
        recent_position = self._position_for_symbol(positions, recent_trade_symbol)
        current_qty = float(current.get("qty") or 0.0)
        recent_qty = float(recent_position.get("qty") or 0.0)
        if current_qty <= 0 and recent_qty > 0:
            return {
                "code": "symbol_context_conflict",
                "reason": f"当前消息标的 {symbol} 与最近交易链 {recent_trade_symbol} 冲突，且当前标的无仓位，已避免盲挂管理动作",
            }
        return None

    def _recent_trade_symbol(self, recent_messages: list[dict[str, Any]]) -> str:
        for item in reversed(recent_messages[-6:]):
            text = str(item.get("text") or item.get("caption") or "").strip().upper()
            if not text:
                continue
            if not any(
                keyword in text
                for keyword in ("LONG", "SHORT", "BUY", "SELL", "ADD", "REDUCE", "PARTIAL", "CLOSE", "出局", "平仓", "开多", "开空", "做多", "做空", "市价多", "市价空")
            ):
                continue
            symbol = self._extract_symbol(text)
            if symbol:
                return symbol
        return ""

    def _extract_symbol(self, text: str) -> str:
        token = text.upper()
        for alias, symbol in {
            "比特币": "BTC-USDT-SWAP",
            "BTC": "BTC-USDT-SWAP",
            "以太坊": "ETH-USDT-SWAP",
            "ETH": "ETH-USDT-SWAP",
            "狗狗币": "DOGE-USDT-SWAP",
            "DOGE": "DOGE-USDT-SWAP",
            "艾达": "ADA-USDT-SWAP",
            "ADA": "ADA-USDT-SWAP",
            "索拉纳": "SOL-USDT-SWAP",
            "SOL": "SOL-USDT-SWAP",
        }.items():
            if alias in token:
                return symbol
        import re

        match = re.search(r"\b([A-Z]{2,15})-USDT-(SWAP|FUTURES)\b", token)
        if match:
            return f"{match.group(1)}-USDT-{match.group(2)}"
        match = re.search(r"\b([A-Z]{2,15})USDT\b", token)
        if match:
            return f"{match.group(1)}-USDT-SWAP"
        match = re.search(r"#([A-Z][A-Z0-9]{1,14})\b", token)
        if match:
            return f"{match.group(1)}-USDT-SWAP"
        return ""

    def _protection_payload(self, intent: TradingIntent) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if intent.tp:
            payload["tp"] = list(intent.tp)
        if intent.sl:
            payload["sl"] = dict(intent.sl)
        if intent.trailing:
            payload["trailing"] = dict(intent.trailing)
        return self._normalized_protection(payload)

    def _normalized_protection(self, protection: Any) -> dict[str, Any]:
        if not isinstance(protection, dict):
            return {}
        normalized: dict[str, Any] = {}
        if isinstance(protection.get("tp"), list):
            normalized["tp"] = [
                {"trigger": float(item.get("trigger"))}
                for item in protection.get("tp", [])
                if isinstance(item, dict) and item.get("trigger") is not None
            ]
        if isinstance(protection.get("sl"), dict) and protection["sl"].get("trigger") is not None:
            normalized["sl"] = {"trigger": float(protection["sl"]["trigger"])}
        if isinstance(protection.get("trailing"), dict) and protection["trailing"].get("trigger") is not None:
            normalized["trailing"] = {"trigger": float(protection["trailing"]["trigger"])}
        return normalized
