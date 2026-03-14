from __future__ import annotations

import json
import os
import shutil
import subprocess
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import CommandResult, ExecutionResult, TradeDecision
from .utils import ensure_parent


class OkxCliAdapter:
    def __init__(self, config: AppConfig):
        self.config = config
        self.okx_bin = os.environ.get("OKX_BIN") or shutil.which("okx") or "/www/server/nodejs/v24.13.0/bin/okx"

    def get_account_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "profile": self.config.okx.profile,
            "site": self.config.okx.site,
            "margin_mode": self.config.okx.margin_mode,
            "position_mode": self.config.okx.position_mode,
            "default_leverage": self.config.okx.default_leverage,
            "okx_config": self._read_okx_config_status(),
            "account_config": self._try_json(self._auth_cmd(["account", "config"])),
            "positions": self._try_json(self._auth_cmd(["swap", "positions"])),
            "open_orders": self._try_json(self._auth_cmd(["swap", "orders"])),
            "algo_orders": self._try_json(self._auth_cmd(["swap", "algo", "orders"])),
        }
        return snapshot

    def get_symbol_snapshot(self, symbol: str | None) -> dict[str, Any]:
        if not symbol:
            return self.get_account_snapshot()
        snapshot = self.get_account_snapshot()
        snapshot["instrument"] = self._try_json([self.okx_bin, "market", "instruments", "--instType", "SWAP", "--instId", symbol, "--json"])
        snapshot["mark_price"] = self._try_json([self.okx_bin, "market", "mark-price", "--instType", "SWAP", "--instId", symbol, "--json"])
        snapshot["positions"] = self._try_json(self._auth_cmd(["swap", "positions", symbol]))
        snapshot["open_orders"] = self._try_json(self._auth_cmd(["swap", "orders", "--instId", symbol]))
        snapshot["algo_orders"] = self._try_json(self._auth_cmd(["swap", "algo", "orders", "--instId", symbol]))
        return snapshot

    def execute(self, decision: TradeDecision) -> ExecutionResult:
        pre = self.get_symbol_snapshot(decision.symbol)
        if decision.kind != "trade" or decision.intent == "ignore":
            return ExecutionResult(mode="skipped", summary="decision ignored", pre_snapshot=pre)

        commands: list[list[str]] = []
        errors: list[str] = []

        try:
            if decision.cancel_existing_orders and decision.symbol:
                commands.extend(self._cancel_existing_commands(pre, decision.symbol))

            intent = decision.intent
            if intent in {"open_long", "open_short", "add_long", "add_short"}:
                commands.extend(self._ensure_leverage_commands(decision.symbol, pre))
                commands.append(self._place_directional_order(decision, intent))
                commands.extend(self._protection_commands_after_entry(decision))
            elif intent == "close_all":
                commands.append(self._close_position_command(decision.symbol, pre))
            elif intent in {"close_partial", "reduce_long", "reduce_short"}:
                commands.append(self._partial_reduce_command(decision, pre))
            elif intent in {"reverse_to_long", "reverse_to_short"}:
                commands.extend(self._reverse_commands(decision, pre))
            elif intent == "update_protection":
                commands.extend(self._replace_protection_commands(decision, pre))
            elif intent == "cancel_orders":
                if decision.symbol:
                    commands.extend(self._cancel_existing_commands(pre, decision.symbol))
            else:
                return ExecutionResult(mode="skipped", summary=f"unsupported intent: {intent}", pre_snapshot=pre)
        except Exception as exc:
            return ExecutionResult(mode="failed", summary=f"planning failed: {exc}", pre_snapshot=pre, errors=[str(exc)])

        if self.config.runtime.dry_run or not self.config.runtime.execution_enabled:
            return ExecutionResult(
                mode="dry-run",
                summary=f"planned {len(commands)} command(s)",
                commands=[self._fake_result(c) for c in commands],
                pre_snapshot=pre,
            )

        results: list[CommandResult] = []
        for cmd in commands:
            result = self._run(cmd)
            results.append(result)
            self._append_audit(result)
            if not result.ok:
                errors.append(result.stderr.strip() or result.stdout.strip() or "unknown OKX CLI error")
                break
        post = self.get_symbol_snapshot(decision.symbol)
        return ExecutionResult(
            mode="executed" if not errors else "failed",
            summary=("executed successfully" if not errors else "execution failed"),
            commands=results,
            pre_snapshot=pre,
            post_snapshot=post,
            errors=errors,
        )

    def _read_okx_config_status(self) -> dict[str, Any]:
        res = self._run([self.okx_bin, "config", "show"])
        return {"ok": res.ok, "stdout": res.stdout.strip(), "stderr": res.stderr.strip()}

    def _auth_cmd(self, args: list[str]) -> list[str]:
        return [self.okx_bin, "--profile", self.config.okx.profile, *args, "--json"]

    def _run(self, cmd: list[str]) -> CommandResult:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return CommandResult(
            command=cmd,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            ok=proc.returncode == 0,
        )

    def _try_json(self, cmd: list[str]) -> Any:
        res = self._run(cmd)
        if not res.ok:
            return {"ok": False, "stderr": res.stderr.strip() or res.stdout.strip()}
        try:
            return json.loads(res.stdout)
        except Exception:
            return {"ok": False, "stdout": res.stdout.strip(), "stderr": "non-json output"}

    def _fake_result(self, cmd: list[str]) -> CommandResult:
        return CommandResult(command=cmd, returncode=0, stdout="DRY_RUN", stderr="", ok=True)

    def _append_audit(self, result: CommandResult) -> None:
        p = ensure_parent(self.config.runtime.okx_audit_path)
        with p.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "command": result.command,
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def _current_position(self, snapshot: dict[str, Any], symbol: str | None) -> dict[str, Any] | None:
        if not symbol:
            return None
        positions = snapshot.get("positions")
        if not isinstance(positions, list):
            return None
        for pos in positions:
            if pos.get("instId") == symbol:
                return pos
        return None

    def _current_algo_orders(self, snapshot: dict[str, Any], symbol: str | None) -> list[dict[str, Any]]:
        data = snapshot.get("algo_orders")
        if not isinstance(data, list) or not symbol:
            return []
        return [row for row in data if row.get("instId") == symbol]

    def _current_open_orders(self, snapshot: dict[str, Any], symbol: str | None) -> list[dict[str, Any]]:
        data = snapshot.get("open_orders")
        if not isinstance(data, list) or not symbol:
            return []
        return [row for row in data if row.get("instId") == symbol]

    def _cancel_existing_commands(self, snapshot: dict[str, Any], symbol: str) -> list[list[str]]:
        commands: list[list[str]] = []
        for row in self._current_open_orders(snapshot, symbol):
            ord_id = row.get("ordId")
            if ord_id:
                commands.append(self._auth_cmd(["swap", "cancel", symbol, "--ordId", str(ord_id)]))
        for row in self._current_algo_orders(snapshot, symbol):
            algo_id = row.get("algoId")
            if algo_id:
                commands.append(self._auth_cmd(["swap", "algo", "cancel", "--instId", symbol, "--algoId", str(algo_id)]))
        return commands

    def _ensure_leverage_commands(self, symbol: str | None, snapshot: dict[str, Any]) -> list[list[str]]:
        if not symbol:
            return []
        commands: list[list[str]] = []
        existing = self._try_json(self._auth_cmd(["swap", "get-leverage", "--instId", symbol, "--mgnMode", self.config.okx.margin_mode]))
        leverage = str(self.config.okx.default_leverage)
        same = False
        if isinstance(existing, list):
            for row in existing:
                if str(row.get("lever")) == leverage:
                    same = True
                    break
        if not same:
            cmd = self._auth_cmd([
                "swap",
                "leverage",
                "--instId",
                symbol,
                "--lever",
                leverage,
                "--mgnMode",
                self.config.okx.margin_mode,
            ])
            commands.append(cmd)
        return commands

    def _place_directional_order(self, decision: TradeDecision, intent: str) -> list[str]:
        symbol = decision.symbol
        if not symbol:
            raise ValueError("symbol required for directional order")
        side = "buy" if intent in {"open_long", "add_long"} else "sell"
        pos_side = self._pos_side_for_intent(intent)
        size = self._resolve_order_size(decision, None, symbol)
        cmd = self._auth_cmd([
            "swap",
            "place",
            "--instId",
            symbol,
            "--side",
            side,
            "--ordType",
            decision.order_type,
            "--sz",
            size,
            "--tdMode",
            self.config.okx.margin_mode,
            "--posSide",
            pos_side,
        ])
        if decision.order_type == "limit" and decision.limit_price is not None:
            cmd.extend(["--px", self._fmt(decision.limit_price)])
        if decision.take_profit_trigger_price is not None:
            cmd.extend(["--tpTriggerPx", self._fmt(decision.take_profit_trigger_price), "--tpOrdPx", "-1"])
        if decision.stop_loss_trigger_price is not None:
            cmd.extend(["--slTriggerPx", self._fmt(decision.stop_loss_trigger_price), "--slOrdPx", "-1"])
        return cmd

    def _close_position_command(self, symbol: str | None, snapshot: dict[str, Any]) -> list[str]:
        if not symbol:
            raise ValueError("symbol required for close")
        pos = self._current_position(snapshot, symbol)
        pos_side = self._position_side_from_snapshot(pos)
        return self._auth_cmd([
            "swap",
            "close",
            "--instId",
            symbol,
            "--mgnMode",
            self.config.okx.margin_mode,
            "--posSide",
            pos_side,
            "--autoCxl",
        ])

    def _partial_reduce_command(self, decision: TradeDecision, snapshot: dict[str, Any]) -> list[str]:
        symbol = decision.symbol
        if not symbol:
            raise ValueError("symbol required for partial reduce")
        pos = self._current_position(snapshot, symbol)
        if not pos:
            raise ValueError(f"no current position for {symbol}")
        position_side = self._position_side_from_snapshot(pos)
        close_side = "sell" if position_side == "long" else "buy"
        size = self._resolve_order_size(decision, pos, symbol)
        return self._auth_cmd([
            "swap",
            "place",
            "--instId",
            symbol,
            "--side",
            close_side,
            "--ordType",
            decision.order_type,
            "--sz",
            size,
            "--tdMode",
            self.config.okx.margin_mode,
            "--posSide",
            position_side,
        ] + (["--px", self._fmt(decision.limit_price)] if decision.order_type == "limit" and decision.limit_price is not None else []))

    def _reverse_commands(self, decision: TradeDecision, snapshot: dict[str, Any]) -> list[list[str]]:
        symbol = decision.symbol
        if not symbol:
            raise ValueError("symbol required for reverse")
        commands: list[list[str]] = []
        current = self._current_position(snapshot, symbol)
        if current:
            commands.append(self._close_position_command(symbol, snapshot))
        commands.extend(self._ensure_leverage_commands(symbol, snapshot))
        open_intent = "open_long" if decision.intent == "reverse_to_long" else "open_short"
        commands.append(self._place_directional_order(decision, open_intent))
        commands.extend(self._protection_commands_after_entry(decision))
        return commands

    def _replace_protection_commands(self, decision: TradeDecision, snapshot: dict[str, Any]) -> list[list[str]]:
        symbol = decision.symbol
        if not symbol:
            raise ValueError("symbol required for protection update")
        pos = self._current_position(snapshot, symbol)
        if not pos:
            raise ValueError(f"no current position for {symbol}")
        size = self._position_size(pos)
        side = "sell" if self._position_side_from_snapshot(pos) == "long" else "buy"
        pos_side = self._position_side_from_snapshot(pos)
        commands = self._cancel_existing_commands(snapshot, symbol)
        if decision.trailing_callback_ratio is not None:
            commands.append(
                self._auth_cmd([
                    "swap",
                    "algo",
                    "trail",
                    "--instId",
                    symbol,
                    "--side",
                    side,
                    "--sz",
                    size,
                    "--callbackRatio",
                    self._fmt(decision.trailing_callback_ratio),
                    "--tdMode",
                    self.config.okx.margin_mode,
                    "--posSide",
                    pos_side,
                    "--reduceOnly",
                ])
            )
            return commands
        if decision.take_profit_trigger_price is None and decision.stop_loss_trigger_price is None:
            return commands
        ord_type = "oco" if decision.take_profit_trigger_price is not None and decision.stop_loss_trigger_price is not None else "conditional"
        cmd = self._auth_cmd([
            "swap",
            "algo",
            "place",
            "--instId",
            symbol,
            "--side",
            side,
            "--sz",
            size,
            "--ordType",
            ord_type,
            "--tdMode",
            self.config.okx.margin_mode,
            "--posSide",
            pos_side,
            "--reduceOnly",
        ])
        if decision.take_profit_trigger_price is not None:
            cmd.extend(["--tpTriggerPx", self._fmt(decision.take_profit_trigger_price), "--tpOrdPx", "-1"])
        if decision.stop_loss_trigger_price is not None:
            cmd.extend(["--slTriggerPx", self._fmt(decision.stop_loss_trigger_price), "--slOrdPx", "-1"])
        commands.append(cmd)
        return commands

    def _protection_commands_after_entry(self, decision: TradeDecision) -> list[list[str]]:
        if decision.symbol is None:
            return []
        if decision.take_profit_trigger_price is None and decision.stop_loss_trigger_price is None and decision.trailing_callback_ratio is None:
            return []
        # attached TP/SL is already included on place; trailing needs a second command after fill, so we skip here.
        return []

    def _resolve_order_size(self, decision: TradeDecision, position: dict[str, Any] | None, symbol: str) -> str:
        if decision.size_mode == "all":
            if not position:
                raise ValueError("size_mode=all requires an existing position")
            return self._position_size(position)
        if decision.size_mode == "percent_position":
            if not position or decision.size_value is None:
                raise ValueError("percent_position requires current position and size_value")
            current = Decimal(self._position_size(position))
            pct = Decimal(str(decision.size_value))
            raw = current * pct
            return self._round_size(symbol, raw)
        if decision.size_mode == "percent_equity" or decision.size_value is None:
            pct = self._clamp_equity_pct(decision.size_value)
            raw = self._contracts_from_equity_pct(symbol, pct)
            return self._round_size(symbol, raw)
        return self._round_size(symbol, Decimal(str(decision.size_value)))

    def _round_size(self, symbol: str, size: Decimal) -> str:
        inst = self._try_json([self.okx_bin, "market", "instruments", "--instType", "SWAP", "--instId", symbol, "--json"])
        if not isinstance(inst, list) or not inst:
            return self._fmt(size)
        row = inst[0]
        lot_sz = Decimal(str(row.get("lotSz", "1")))
        min_sz = Decimal(str(row.get("minSz", lot_sz)))
        rounded = (size / lot_sz).to_integral_value(rounding=ROUND_DOWN) * lot_sz
        if rounded < min_sz:
            rounded = min_sz
        return self._fmt(rounded)

    def _contracts_from_equity_pct(self, symbol: str, pct: Decimal) -> Decimal:
        balance = self._try_json(self._auth_cmd(["account", "balance", "USDT"]))
        if not isinstance(balance, list) or not balance:
            raise ValueError("could not read account balance for percent_equity sizing")
        row = balance[0]
        details = row.get("details") or []
        usdt_detail = next((d for d in details if d.get("ccy") == "USDT"), None)
        if not usdt_detail:
            raise ValueError("USDT balance not found for percent_equity sizing")
        available_usdt = Decimal(str(usdt_detail.get("availBal") or usdt_detail.get("availEq") or usdt_detail.get("eq") or "0"))
        if available_usdt <= 0:
            raise ValueError("available USDT is zero; cannot size order")

        instrument = self._try_json([self.okx_bin, "market", "instruments", "--instType", "SWAP", "--instId", symbol, "--json"])
        mark_price = self._try_json([self.okx_bin, "market", "mark-price", "--instType", "SWAP", "--instId", symbol, "--json"])
        if not isinstance(instrument, list) or not instrument:
            raise ValueError(f"instrument metadata unavailable for {symbol}")
        if not isinstance(mark_price, list) or not mark_price:
            raise ValueError(f"mark price unavailable for {symbol}")

        row = instrument[0]
        mark = Decimal(str(mark_price[0].get("markPx") or "0"))
        ct_val = Decimal(str(row.get("ctVal") or "0"))
        leverage = Decimal(str(self.config.okx.default_leverage))
        if mark <= 0 or ct_val <= 0 or leverage <= 0:
            raise ValueError(f"invalid sizing inputs for {symbol}: mark={mark}, ctVal={ct_val}, leverage={leverage}")

        notional_usdt = available_usdt * pct * leverage
        contracts = notional_usdt / (mark * ct_val)
        return contracts

    def _clamp_equity_pct(self, requested: float | None) -> Decimal:
        min_pct = Decimal(str(self.config.okx.min_position_pct))
        max_pct = Decimal(str(self.config.okx.max_position_pct))
        default_pct = Decimal(str(self.config.okx.default_position_pct))
        pct = Decimal(str(requested)) if requested is not None else default_pct
        if pct < min_pct:
            pct = min_pct
        if pct > max_pct:
            pct = max_pct
        return pct

    @staticmethod
    def _position_size(position: dict[str, Any]) -> str:
        for key in ("pos", "size", "sz"):
            if key in position:
                return str(position[key])
        raise ValueError(f"could not determine position size from {position}")

    def _pos_side_for_intent(self, intent: str) -> str:
        if self.config.okx.position_mode == "net_mode":
            return "net"
        return "long" if intent in {"open_long", "add_long"} else "short"

    def _position_side_from_snapshot(self, position: dict[str, Any] | None) -> str:
        if self.config.okx.position_mode == "net_mode":
            return "net"
        if not position:
            return "long"
        for key in ("posSide", "side", "direction"):
            value = str(position.get(key) or "").lower()
            if value in {"long", "short", "net"}:
                return value
        return "long"

    @staticmethod
    def _fmt(value: Decimal | float | str) -> str:
        if isinstance(value, Decimal):
            s = format(value.normalize(), "f")
        else:
            s = format(Decimal(str(value)).normalize(), "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
