from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from .config import AppConfig
from .models import NormalizedMessage, TradingIntent


class AIError(RuntimeError):
    pass


class OpenClawAI:
    def __init__(self, config: AppConfig):
        self.config = config

    def parse(self, message: NormalizedMessage, recent_messages: list[dict[str, Any]], account_state: dict[str, Any]) -> TradingIntent:
        prompt = self._build_prompt(message, recent_messages, account_state)
        if self.config.ai.provider == "heuristic":
            return self._heuristic_parse(message)
        try:
            raw = self._run_openclaw(prompt)
            payload = _extract_json(raw)
            return self._intent_from_payload(payload)
        except Exception:
            return self._heuristic_parse(message)

    def _run_openclaw(self, prompt: str) -> str:
        cmd = [
            "openclaw",
            "agent",
            "--local",
            "--message",
            prompt,
            "--thinking",
            self.config.ai.thinking if self.config.ai.thinking != "custom" else "high",
            "--json",
        ]
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=self.config.ai.timeout_seconds,
        )
        payload = json.loads(result.stdout)
        for key in ("reply", "message", "text", "output"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return result.stdout

    def _build_prompt(self, message: NormalizedMessage, recent_messages: list[dict[str, Any]], account_state: dict[str, Any]) -> str:
        schema = {
            "executable": True,
            "action": "open_long",
            "symbol": "BTC-USDT-SWAP",
            "market_type": "swap",
            "side": "buy",
            "entry_type": "market",
            "size_mode": "fixed_usdt",
            "size_value": 100.0,
            "leverage": self.config.trading.default_leverage,
            "margin_mode": self.config.trading.margin_mode,
            "risk_level": "medium",
            "tp": [],
            "sl": None,
            "trailing": None,
            "require_manual_confirmation": False,
            "confidence": 0.75,
            "reason": "brief explanation"
        }
        return (
            f"{self.config.ai.system_prompt}\n"
            "Return a single JSON object with exactly these keys:\n"
            f"{json.dumps(schema, ensure_ascii=True)}\n"
            f"Message:\n{json.dumps(message.to_dict(), ensure_ascii=True)}\n"
            f"Recent channel context:\n{json.dumps(recent_messages[-5:], ensure_ascii=True)}\n"
            f"Account state:\n{json.dumps(account_state, ensure_ascii=True)}"
        )

    def _heuristic_parse(self, message: NormalizedMessage) -> TradingIntent:
        text = message.content_text().strip()
        upper = text.upper()
        symbol = _extract_symbol(upper)
        leverage = _extract_leverage(upper) or self.config.trading.default_leverage
        size_value = _extract_size_value(upper) or 100.0
        tp = _extract_protection_levels(upper, ("TP", "TAKE PROFIT", "TARGET"))
        sl = _extract_single_level(upper, ("SL", "STOP LOSS", "STOP"))
        trailing = _extract_single_level(upper, ("TRAILING", "TS"))
        if trailing is not None:
            trailing = {"trigger": trailing}
        if "IGNORE" in upper or not upper:
            payload = {
                "executable": False,
                "action": "ignore",
                "symbol": symbol,
                "market_type": "swap",
                "side": "flat",
                "entry_type": "market",
                "size_mode": "fixed_usdt",
                "size_value": 0.0,
                "leverage": leverage,
                "margin_mode": self.config.trading.margin_mode,
                "risk_level": "low",
                "tp": [],
                "sl": None,
                "trailing": trailing,
                "require_manual_confirmation": False,
                "confidence": 0.1,
                "reason": "No actionable trade signal detected.",
            }
            return self._intent_from_payload(payload)
        is_short_bias = any(word in upper for word in ("SHORT", "SELL", "BEAR"))
        has_trade_open_keyword = any(word in upper for word in ("LONG", "SHORT", "BUY", "SELL", "ADD", "REVERSE", "FLIP"))
        has_protection_update = any(
            word in upper for word in ("PROTECTION", "STOP LOSS", "TAKE PROFIT", " TRAILING", "UPDATE TP", "UPDATE SL")
        )
        action = "open_short" if is_short_bias else "open_long"
        side = "sell" if is_short_bias else "buy"
        reason = "Heuristic parser fallback inferred a trade intent from the message."

        if "CANCEL" in upper:
            action = "cancel_orders"
            side = "flat"
            reason = "Heuristic parser inferred an order cancel request."
        elif has_protection_update and not has_trade_open_keyword and "CLOSE" not in upper and "REDUCE" not in upper:
            action = "update_protection"
            side = "flat"
            reason = "Heuristic parser inferred a protection update."
        elif "REVERSE" in upper or "FLIP" in upper:
            action = "reverse_to_short" if is_short_bias else "reverse_to_long"
            side = "sell" if is_short_bias else "buy"
            reason = "Heuristic parser inferred a position reversal."
        elif "REDUCE" in upper or "PARTIAL" in upper:
            action = "reduce_short" if is_short_bias else "reduce_long"
            side = "buy" if is_short_bias else "sell"
            reason = "Heuristic parser inferred a position reduction."
        elif "CLOSE" in upper:
            action = "close_all"
            side = "flat"
            reason = "Heuristic parser inferred a close-all request."
        elif "ADD" in upper:
            action = "add_short" if is_short_bias else "add_long"
            side = "sell" if is_short_bias else "buy"
            reason = "Heuristic parser inferred a position add request."
        payload = {
            "executable": True,
            "action": action,
            "symbol": symbol,
            "market_type": "swap",
            "side": side,
            "entry_type": "market",
            "size_mode": "fixed_usdt",
            "size_value": size_value,
            "leverage": leverage,
            "margin_mode": self.config.trading.margin_mode,
            "risk_level": "medium",
            "tp": tp,
            "sl": {"trigger": sl} if sl is not None else None,
            "trailing": trailing,
            "require_manual_confirmation": False,
            "confidence": 0.55,
            "reason": reason,
        }
        return self._intent_from_payload(payload)

    def _intent_from_payload(self, payload: dict[str, Any]) -> TradingIntent:
        required = {
            "executable", "action", "symbol", "market_type", "side", "entry_type",
            "size_mode", "size_value", "leverage", "margin_mode", "risk_level",
            "require_manual_confirmation", "confidence", "reason"
        }
        missing = required - set(payload)
        if missing:
            raise AIError(f"AI output missing fields: {sorted(missing)}")
        return TradingIntent(
            executable=bool(payload["executable"]),
            action=str(payload["action"]),
            symbol=str(payload["symbol"]),
            market_type=str(payload["market_type"]),
            side=str(payload["side"]),
            entry_type=str(payload["entry_type"]),
            size_mode=str(payload["size_mode"]),
            size_value=float(payload["size_value"]),
            leverage=int(payload["leverage"]),
            margin_mode=str(payload["margin_mode"]),
            risk_level=str(payload["risk_level"]),
            tp=list(payload.get("tp", [])),
            sl=payload.get("sl"),
            trailing=payload.get("trailing"),
            require_manual_confirmation=bool(payload["require_manual_confirmation"]),
            confidence=float(payload["confidence"]),
            reason=str(payload["reason"]),
            raw=payload,
        )


def _extract_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9]*\n|\n```$", "", raw, flags=re.MULTILINE)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise AIError("AI output did not contain JSON")
    return json.loads(raw[start : end + 1])


def _extract_symbol(text: str) -> str:
    match = re.search(r"\b([A-Z]{2,10})-USDT-(SWAP|FUTURES)\b", text)
    if match:
        return f"{match.group(1)}-USDT-{match.group(2)}"
    match = re.search(r"\b([A-Z]{2,10})USDT\b", text)
    token = match.group(1) if match else "BTC"
    return f"{token}-USDT-SWAP"


def _extract_leverage(text: str) -> int | None:
    match = re.search(r"\b(\d{1,3})\s*X\b", text)
    if not match:
        return None
    return int(match.group(1))


def _extract_size_value(text: str) -> float | None:
    patterns = (
        r"\bSIZE\s*[:=]?\s*(\d+(?:\.\d+)?)\b",
        r"\bUSDT\s*[:=]?\s*(\d+(?:\.\d+)?)\b",
        r"\$(\d+(?:\.\d+)?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None


def _extract_protection_levels(text: str, labels: tuple[str, ...]) -> list[dict[str, Any]]:
    levels = []
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[:=]?\s*(\d+(?:\.\d+)?)"
        for match in re.finditer(pattern, text):
            levels.append({"trigger": float(match.group(1))})
    return levels


def _extract_single_level(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*[:=]?\s*(\d+(?:\.\d+)?)"
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return None
