from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import AppConfig, resolve_okx_credentials
from .models import TradingIntent, utc_now


SIMULATED_SUPPORTED_ACTIONS = (
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
)

REAL_DEMO_SUPPORTED_ACTIONS = (
    "open_long",
    "open_short",
    "add_long",
    "add_short",
    "reduce_long",
    "reduce_short",
    "reverse_to_long",
    "reverse_to_short",
    "close_all",
    "cancel_orders",
)


@dataclass
class ExecutionResult:
    status: str
    exchange_order_id: str
    payload: dict[str, Any]
    position_snapshot: dict[str, Any] | None = None


class OKXGateway:
    def __init__(self, config: AppConfig):
        self.config = config
        self._demo_positions: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._leverage_cache: set[tuple[str, str, int]] = set()

    def set_config(self, config: AppConfig) -> None:
        self.config = config
        self._leverage_cache.clear()

    def restore_simulated_state(self, positions: list[dict[str, Any]], counter: int = 0) -> None:
        restored: dict[str, dict[str, Any]] = {}
        for item in positions:
            symbol = str(item.get("symbol", ""))
            payload = item.get("payload") if isinstance(item, dict) else None
            if not symbol or not isinstance(payload, dict):
                continue
            restored[symbol] = payload.copy()
        self._demo_positions = restored
        self._counter = max(self._counter, counter)

    def reset_local_state(self) -> None:
        self._demo_positions = {}
        self._counter = 0
        self._leverage_cache.clear()

    def execute(self, intent: TradingIntent, *, force_simulated: bool = False) -> ExecutionResult:
        api_key, _, _ = resolve_okx_credentials(self.config)
        if force_simulated:
            return self._execute_simulated(intent)
        if self.config.okx.enabled and not self.config.okx.use_demo:
            raise RuntimeError("Live OKX mode is disabled in this demo-only build")
        if self.config.okx.enabled and api_key:
            return self._execute_real_demo(intent)
        return self._execute_simulated(intent)

    def _execute_simulated(self, intent: TradingIntent) -> ExecutionResult:
        self._counter += 1
        order_id = f"demo-{self._counter:06d}"
        symbol = intent.symbol
        existing, qty = self._apply_intent_to_position(intent)
        existing["source"] = "simulated_demo"
        state = "canceled" if intent.action == "cancel_orders" else "filled"
        return ExecutionResult(
            status=state,
            exchange_order_id=order_id,
            payload={
                "environment": "simulated_demo",
                "execution_path": "simulated_demo",
                "instId": symbol,
                "ordId": order_id,
                "state": state,
                "side": intent.side,
                "action": intent.action,
                "sz": qty,
                "lever": intent.leverage,
                "protection": existing.get("protection", {}),
                "attached_algo_orders": _attached_algo_orders(existing.get("protection", {})),
            },
            position_snapshot=existing.copy(),
        )

    def _execute_real_demo(self, intent: TradingIntent) -> ExecutionResult:
        if intent.action == "cancel_orders":
            payload, exchange_order_id, attached_algo_orders = self._execute_real_demo_cancel_orders(intent)
            status = "canceled"
        else:
            status = "filled"
            if intent.action in {"reverse_to_long", "reverse_to_short"}:
                self._ensure_real_demo_leverage(intent)
                payload, exchange_order_id, attached_algo_orders = self._execute_real_demo_reverse(intent)
            else:
                if intent.action == "close_all":
                    self.sync_real_demo_position(intent.symbol)
                else:
                    self._ensure_real_demo_leverage(intent)
                body = self._build_real_order_body(intent)
                payload = self._request("POST", "/api/v5/trade/order", body)
                first = self._validate_real_demo_order(payload)
                exchange_order_id = str(first.get("ordId", ""))
                attached_algo_orders = list(body.get("attachAlgoOrds") or [])
        position_snapshot, _ = self._apply_intent_to_position(intent)
        position_snapshot = position_snapshot.copy()
        position_snapshot["source"] = "local_expected"
        position_snapshot["exchange_protection_orders"] = list(attached_algo_orders)
        self._demo_positions[intent.symbol] = position_snapshot.copy()
        return ExecutionResult(
            status=status,
            exchange_order_id=exchange_order_id,
            payload={
                **payload,
                "execution_path": "real_demo_rest",
                "attached_algo_orders": attached_algo_orders,
            },
            position_snapshot=position_snapshot,
        )

    def _ensure_real_demo_leverage(self, intent: TradingIntent) -> None:
        cache_key = (intent.symbol, intent.margin_mode, intent.leverage)
        if cache_key in self._leverage_cache:
            return
        payload = self._request(
            "POST",
            "/api/v5/account/set-leverage",
            {
                "lever": str(intent.leverage),
                "mgnMode": intent.margin_mode,
                "instId": intent.symbol,
            },
        )
        if str(payload.get("code", "0")) != "0":
            raise RuntimeError(
                _with_okx_environment_hint(
                    payload.get("msg", "OKX demo leverage configuration failed"),
                    payload=payload,
                )
            )
        self._leverage_cache.add(cache_key)

    def _request(self, method: str, path: str, body: Any | None = None) -> dict[str, Any]:
        api_key, api_secret, passphrase = resolve_okx_credentials(self.config)
        body_text = "" if body is None else json.dumps(body)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        prehash = f"{timestamp}{method}{path}{body_text if method != 'GET' else ''}"
        signature = base64.b64encode(
            hmac.new(
                api_secret.encode("utf-8"),
                prehash.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        url = f"{self.config.okx.rest_base}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("OK-ACCESS-KEY", api_key)
        req.add_header("OK-ACCESS-SIGN", signature)
        req.add_header("OK-ACCESS-TIMESTAMP", timestamp)
        req.add_header("OK-ACCESS-PASSPHRASE", passphrase)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "tg-okx-auto-trade/1.0")
        if self.config.okx.use_demo:
            req.add_header("x-simulated-trading", "1")
        request_data = None if method == "GET" else body_text.encode("utf-8")
        try:
            with urllib.request.urlopen(req, data=request_data, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_okx_http_error_detail(method=method, path=path, exc=exc)) from exc

    def sync_real_demo_position(self, symbol: str) -> dict[str, Any]:
        current = (self._demo_positions.get(symbol) or {}).copy()
        payload = self._request("GET", f"/api/v5/account/positions?instId={urllib.parse.quote(symbol)}")
        if str(payload.get("code", "0")) != "0":
            raise RuntimeError(_with_okx_environment_hint(payload.get("msg", "OKX demo positions query failed"), payload=payload))
        rows = payload.get("data") or []
        row = next((item for item in rows if str(item.get("instId", "")) == symbol), rows[0] if rows else {})
        pos = float(row.get("pos") or 0.0)
        side = "long" if pos > 0 else "short" if pos < 0 else "flat"
        snapshot = {
            "symbol": symbol,
            "qty": abs(pos),
            "side": side,
            "avg_price": float(row.get("avgPx") or 0.0),
            "margin_mode": str(row.get("mgnMode") or current.get("margin_mode") or self.config.trading.margin_mode),
            "leverage": int(float(row.get("lever") or current.get("leverage") or self.config.trading.default_leverage)),
            "realized_pnl": float(current.get("realized_pnl") or 0.0),
            "unrealized_pnl": float(row.get("upl") or 0.0),
            "protection": current.get("protection", {}) if abs(pos) > 0 else {},
            "exchange_protection_orders": current.get("exchange_protection_orders", []) if abs(pos) > 0 else [],
            "source": "exchange_polled",
            "last_action": current.get("last_action", "sync_position"),
            "updated_at": utc_now(),
        }
        self._demo_positions[symbol] = snapshot
        return snapshot

    def positions(self) -> list[dict[str, Any]]:
        return [item.copy() for item in self._demo_positions.values()]

    def action_support(self) -> dict[str, list[str]]:
        return {
            "simulated_demo": list(SIMULATED_SUPPORTED_ACTIONS),
            "real_demo_rest": list(REAL_DEMO_SUPPORTED_ACTIONS),
        }

    def _execute_real_demo_reverse(self, intent: TradingIntent) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        current = self._demo_positions.get(intent.symbol)
        current_qty = max(float((current or {}).get("qty", 0.0)), 0.0)
        current_side = str((current or {}).get("side", "flat"))
        desired_side = "long" if intent.action == "reverse_to_long" else "short"
        desired_qty = max(float(intent.size_value), 0.0)
        steps: list[dict[str, Any]] = []
        attached_algo_orders: list[dict[str, Any]] = []

        if current_side == desired_side:
            delta = round(desired_qty - current_qty, 4)
            if delta > 0:
                steps.append(
                    self._submit_real_demo_order(
                        symbol=intent.symbol,
                        margin_mode=intent.margin_mode,
                        side="buy" if desired_side == "long" else "sell",
                        qty=delta,
                        entry_type=intent.entry_type,
                        reduce_only=False,
                        action_label=f"reverse_expand_{desired_side}",
                        attach_algo_orders=self._build_real_attached_algo_orders(intent),
                    )
                )
                attached_algo_orders = list(steps[-1]["request_body"].get("attachAlgoOrds") or [])
            elif delta < 0:
                steps.append(
                    self._submit_real_demo_order(
                        symbol=intent.symbol,
                        margin_mode=intent.margin_mode,
                        side="sell" if desired_side == "long" else "buy",
                        qty=abs(delta),
                        entry_type="market",
                        reduce_only=True,
                        action_label=f"reverse_trim_{desired_side}",
                    )
                )
        else:
            if current_qty > 0 and current_side in {"long", "short"}:
                steps.append(
                    self._submit_real_demo_order(
                        symbol=intent.symbol,
                        margin_mode=intent.margin_mode,
                        side="sell" if current_side == "long" else "buy",
                        qty=current_qty,
                        entry_type="market",
                        reduce_only=True,
                        action_label=f"reverse_close_{current_side}",
                    )
                )
            if desired_qty > 0:
                steps.append(
                    self._submit_real_demo_order(
                        symbol=intent.symbol,
                        margin_mode=intent.margin_mode,
                        side="buy" if desired_side == "long" else "sell",
                        qty=desired_qty,
                        entry_type=intent.entry_type,
                        reduce_only=False,
                        action_label=f"reverse_open_{desired_side}",
                        attach_algo_orders=self._build_real_attached_algo_orders(intent),
                    )
                )
                attached_algo_orders = list(steps[-1]["request_body"].get("attachAlgoOrds") or [])

        if not steps:
            raise RuntimeError(
                f"Reverse action {intent.action} would not change the local expected position for {intent.symbol}"
            )

        order_ids = [step["exchange_order_id"] for step in steps if step.get("exchange_order_id")]
        return {
            "code": "0",
            "msg": "",
            "data": [item for step in steps for item in step["response"].get("data", [])],
            "steps": steps,
            "attached_algo_orders": attached_algo_orders,
        }, (order_ids[-1] if order_ids else ""), attached_algo_orders

    def _submit_real_demo_order(
        self,
        *,
        symbol: str,
        margin_mode: str,
        side: str,
        qty: float,
        entry_type: str,
        reduce_only: bool,
        action_label: str,
        attach_algo_orders: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        rounded_qty = round(float(qty), 4)
        if rounded_qty <= 0:
            raise RuntimeError(f"OKX demo {action_label} has no executable quantity")
        body = {
            "instId": symbol,
            "tdMode": "isolated" if margin_mode == "isolated" else "cross",
            "side": side,
            "ordType": "market" if entry_type == "market" else "limit",
            "sz": str(rounded_qty),
            "clOrdId": self._client_order_id(symbol=symbol, action=action_label, side=side, size=rounded_qty),
        }
        if reduce_only:
            body["reduceOnly"] = "true"
        if attach_algo_orders:
            body["attachAlgoOrds"] = list(attach_algo_orders)
        payload = self._request("POST", "/api/v5/trade/order", body)
        first = self._validate_real_demo_order(payload)
        return {
            "action": action_label,
            "request_body": body,
            "response": payload,
            "exchange_order_id": str(first.get("ordId", "")),
        }

    def _validate_real_demo_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload.get("code", "0")) != "0":
            raise RuntimeError(_with_okx_environment_hint(payload.get("msg", "OKX demo request failed"), payload=payload))
        first = payload.get("data", [{}])[0]
        if str(first.get("sCode", "0")) != "0":
            raise RuntimeError(_with_okx_environment_hint(first.get("sMsg", "OKX demo order rejected"), payload=first))
        return first

    def _execute_real_demo_cancel_orders(self, intent: TradingIntent) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        current = self._demo_positions.get(intent.symbol) or {}
        tracked_orders = [
            item.copy()
            for item in list(current.get("exchange_protection_orders") or [])
            if isinstance(item, dict)
        ]
        cancel_requests: list[dict[str, Any]] = []
        for item in tracked_orders:
            algo_client_id = str(item.get("attachAlgoClOrdId") or item.get("algoClOrdId") or "").strip()
            if not algo_client_id:
                continue
            cancel_request = {"instId": intent.symbol, "algoClOrdId": algo_client_id}
            ord_type = _okx_algo_order_type(item)
            if ord_type:
                cancel_request["ordType"] = ord_type
            cancel_requests.append(cancel_request)

        exchange_order_id = self._client_order_id(symbol=intent.symbol, action="cancel_orders", side=intent.side, size=0.0)
        if not cancel_requests:
            return {
                "code": "0",
                "msg": "",
                "data": [],
                "cancel_mode": "local_expected_no_tracked_algo_ids",
                "detail": "No locally tracked OKX attached protection ids were available to cancel.",
            }, exchange_order_id, []

        payload = self._request("POST", "/api/v5/trade/cancel-algos", cancel_requests)
        self._validate_real_demo_cancel(payload)
        payload = {
            **payload,
            "cancel_mode": "okx_demo_rest",
            "canceled_algo_orders": cancel_requests,
        }
        return payload, exchange_order_id, []

    def _validate_real_demo_cancel(self, payload: dict[str, Any]) -> None:
        if str(payload.get("code", "0")) != "0":
            raise RuntimeError(_with_okx_environment_hint(payload.get("msg", "OKX demo cancel request failed"), payload=payload))
        for item in payload.get("data", []):
            if str(item.get("sCode", "0")) != "0":
                raise RuntimeError(_with_okx_environment_hint(item.get("sMsg", "OKX demo cancel request rejected"), payload=item))

    def _apply_intent_to_position(self, intent: TradingIntent) -> tuple[dict[str, Any], float]:
        symbol = intent.symbol
        existing = self._demo_positions.get(
            symbol,
            {
                "symbol": symbol,
                "qty": 0.0,
                "side": "flat",
                "avg_price": 0.0,
                "margin_mode": intent.margin_mode,
                "leverage": intent.leverage,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "protection": {},
                "exchange_protection_orders": [],
                "updated_at": utc_now(),
            },
        ).copy()
        current_qty = float(existing.get("qty", 0.0))
        qty = max(float(intent.size_value), 0.0)
        if intent.action in {"open_long", "add_long"}:
            existing["qty"] = current_qty + qty
            existing["side"] = "long"
            existing["avg_price"] = 100.0
        elif intent.action in {"open_short", "add_short"}:
            existing["qty"] = current_qty + qty
            existing["side"] = "short"
            existing["avg_price"] = 100.0
        elif intent.action == "reduce_long":
            qty = self._clamp_reduce_size(existing, qty, "long")
            existing["qty"] = max(0.0, current_qty - qty)
            existing["side"] = "flat" if existing["qty"] == 0.0 else "long"
        elif intent.action == "reduce_short":
            qty = self._clamp_reduce_size(existing, qty, "short")
            existing["qty"] = max(0.0, current_qty - qty)
            existing["side"] = "flat" if existing["qty"] == 0.0 else "short"
        elif intent.action == "close_all":
            qty = max(current_qty, 0.0)
            existing["qty"] = 0.0
            existing["side"] = "flat"
        elif intent.action == "reverse_to_long":
            existing["qty"] = qty
            existing["side"] = "long" if qty > 0 else "flat"
            existing["avg_price"] = 100.0 if qty > 0 else 0.0
        elif intent.action == "reverse_to_short":
            existing["qty"] = qty
            existing["side"] = "short" if qty > 0 else "flat"
            existing["avg_price"] = 100.0 if qty > 0 else 0.0
        elif intent.action == "cancel_orders":
            qty = 0.0
            existing["protection"] = {}
            existing["exchange_protection_orders"] = []
        elif intent.action == "update_protection":
            qty = 0.0
            existing["protection"] = _protection_payload(intent)
            existing["exchange_protection_orders"] = _attached_algo_orders(existing["protection"])
        else:
            raise RuntimeError(f"Action {intent.action} is not supported by the OKX gateway in this build")
        if intent.action in {
            "open_long",
            "add_long",
            "open_short",
            "add_short",
            "reverse_to_long",
            "reverse_to_short",
        }:
            protection = _protection_payload(intent)
            if protection:
                existing["protection"] = protection
                existing["exchange_protection_orders"] = _attached_algo_orders(protection)
        if existing["qty"] == 0.0:
            existing["avg_price"] = 0.0
            if intent.action in {"close_all", "reduce_long", "reduce_short", "cancel_orders"}:
                existing["protection"] = {}
                existing["exchange_protection_orders"] = []
        existing["margin_mode"] = intent.margin_mode
        existing["leverage"] = intent.leverage
        existing["last_action"] = intent.action
        existing["updated_at"] = utc_now()
        self._demo_positions[symbol] = existing
        return existing, round(qty, 4)

    def _build_real_order_body(self, intent: TradingIntent) -> dict[str, Any]:
        current = self._demo_positions.get(intent.symbol)
        if intent.action in {"reverse_to_long", "reverse_to_short", "update_protection"}:
            raise RuntimeError(f"Action {intent.action} is not supported by the OKX demo REST path in this build")

        if intent.action in {"open_long", "add_long"}:
            side = "buy"
            qty = max(float(intent.size_value), 0.0)
            reduce_only = False
        elif intent.action in {"open_short", "add_short"}:
            side = "sell"
            qty = max(float(intent.size_value), 0.0)
            reduce_only = False
        elif intent.action == "reduce_long":
            side = "sell"
            qty = self._clamp_reduce_size(current, intent.size_value, "long")
            reduce_only = True
        elif intent.action == "reduce_short":
            side = "buy"
            qty = self._clamp_reduce_size(current, intent.size_value, "short")
            reduce_only = True
        elif intent.action == "close_all":
            side, qty = self._close_order_params(current)
            reduce_only = True
        else:
            raise RuntimeError(f"Action {intent.action} is not supported by the OKX demo REST path in this build")

        if qty <= 0:
            raise RuntimeError(f"No open quantity available for {intent.action} on {intent.symbol}")

        body = {
            "instId": intent.symbol,
            "tdMode": "isolated" if intent.margin_mode == "isolated" else "cross",
            "side": side,
            "ordType": "market" if intent.entry_type == "market" else "limit",
            "sz": str(round(qty, 4)),
            "clOrdId": self._client_order_id(intent),
        }
        if reduce_only:
            body["reduceOnly"] = "true"
        attached_algo_orders = self._build_real_attached_algo_orders(intent)
        if attached_algo_orders:
            body["attachAlgoOrds"] = attached_algo_orders
        return body

    def _build_real_attached_algo_orders(self, intent: TradingIntent) -> list[dict[str, Any]]:
        protection = _protection_payload(intent)
        if not protection:
            return []
        if protection.get("trailing"):
            raise RuntimeError(
                "OKX demo REST trailing protection is not supported in this build; use the simulated path for trailing updates."
            )
        attached: list[dict[str, Any]] = []
        for index, take_profit in enumerate(protection.get("tp", []), start=1):
            attached.append(
                {
                    "attachAlgoClOrdId": self._client_order_id(
                        symbol=intent.symbol,
                        action=f"tp_{index}",
                        side=intent.side,
                        size=float(intent.size_value),
                    ),
                    "tpTriggerPx": _absolute_trigger_price(take_profit, action="take-profit"),
                    "tpOrdPx": "-1",
                }
            )
        if protection.get("sl"):
            attached.append(
                {
                    "attachAlgoClOrdId": self._client_order_id(
                        symbol=intent.symbol,
                        action="sl",
                        side=intent.side,
                        size=float(intent.size_value),
                    ),
                    "slTriggerPx": _absolute_trigger_price(protection["sl"], action="stop-loss"),
                    "slOrdPx": "-1",
                }
            )
        return attached

    def _client_order_id(
        self,
        intent: TradingIntent | None = None,
        *,
        symbol: str | None = None,
        action: str | None = None,
        side: str | None = None,
        size: float | None = None,
    ) -> str:
        if intent is not None:
            symbol = intent.symbol
            action = intent.action
            side = intent.side
            size = round(float(intent.size_value), 4)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "symbol": symbol,
                    "action": action,
                    "side": side,
                    "size": size,
                    "ts": int(time.time() * 1000),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return f"tg{digest[:30]}"

    def _clamp_reduce_size(self, current: dict[str, Any] | None, requested: float, expected_side: str) -> float:
        if not current or current.get("side") != expected_side:
            return 0.0
        current_qty = max(float(current.get("qty", 0.0)), 0.0)
        return min(max(float(requested), 0.0), current_qty)

    def _close_order_params(self, current: dict[str, Any] | None) -> tuple[str, float]:
        if not current:
            return "sell", 0.0
        current_qty = max(float(current.get("qty", 0.0)), 0.0)
        side = str(current.get("side", "flat"))
        if side == "long":
            return "sell", current_qty
        if side == "short":
            return "buy", current_qty
        return "sell", 0.0


def _protection_payload(intent: TradingIntent) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if intent.tp:
        payload["tp"] = list(intent.tp)
    if intent.sl:
        payload["sl"] = dict(intent.sl)
    if intent.trailing:
        payload["trailing"] = dict(intent.trailing)
    return payload


def _attached_algo_orders(protection: dict[str, Any]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for item in protection.get("tp", []):
        orders.append({"type": "tp", **item})
    if protection.get("sl"):
        orders.append({"type": "sl", **protection["sl"]})
    if protection.get("trailing"):
        orders.append({"type": "trailing", **protection["trailing"]})
    return orders


def _absolute_trigger_price(payload: dict[str, Any], *, action: str) -> str:
    if "trigger" in payload:
        return str(payload["trigger"])
    mode = str(payload.get("mode", "") or "")
    if mode == "global_ratio" or "ratio" in payload:
        raise RuntimeError(
            f"OKX demo REST {action} protection requires an absolute trigger price; ratio-based protection remains simulated-only in this build"
        )
    raise RuntimeError(
        f"OKX demo REST {action} protection requires a trigger price in this build"
    )


def _okx_algo_order_type(payload: dict[str, Any]) -> str:
    order_type = str(payload.get("type", "") or "")
    if order_type in {"tp", "sl"}:
        return "conditional"
    if "tpTriggerPx" in payload or "slTriggerPx" in payload:
        return "conditional"
    if order_type == "trailing":
        return "move_order_stop"
    return ""


def _okx_http_error_detail(*, method: str, path: str, exc: urllib.error.HTTPError) -> str:
    response_body = ""
    try:
        response_body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        response_body = ""
    status_detail = f"HTTP {exc.code}"
    if exc.reason:
        status_detail = f"{status_detail} {exc.reason}"
    detail = f"OKX demo REST {method} {path} failed with {status_detail}"
    if response_body:
        detail = f"{detail}; response body: {response_body}"
    return _with_okx_environment_hint(detail, response_body=response_body)


def _with_okx_environment_hint(detail: str, *, response_body: str = "", payload: dict[str, Any] | None = None) -> str:
    if not _is_okx_environment_mismatch(response_body=response_body, payload=payload):
        return detail
    hint = (
        "Likely cause: this app is using the OKX demo path with `x-simulated-trading: 1`, "
        "but the supplied API key belongs to the live environment or another wrong environment key."
    )
    if hint in detail:
        return detail
    return f"{detail}; {hint}"


def _is_okx_environment_mismatch(*, response_body: str = "", payload: dict[str, Any] | None = None) -> bool:
    response_text = str(response_body or "")
    payload_text = json.dumps(payload, sort_keys=True) if payload else ""
    combined = f"{response_text} {payload_text}"
    return "50101" in combined or "APIKey does not match current environment." in combined
