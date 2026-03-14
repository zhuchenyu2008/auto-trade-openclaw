#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state" / "hourly_report_state.json"
TZ = ZoneInfo("Asia/Shanghai")
OKX_BIN = os.environ.get("OKX_BIN") or shutil.which("okx") or "/www/server/nodejs/v24.13.0/bin/okx"


@dataclass
class Snapshot:
    total_eq: Decimal
    usdt_eq: Decimal
    usdt_avail: Decimal
    contract_unrealized_pnl: Decimal
    positions: list[dict]
    history_entries: list[dict]
    spot_non_usdt_value: Decimal
    spot_assets: list[dict]


def run_okx(*args: str) -> object:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    profile = cfg["okx"]["profile"]
    cmd = [OKX_BIN, "--profile", profile, *args, "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {' '.join(cmd)}")
    return json.loads(proc.stdout)


def to_decimal(value: object) -> Decimal:
    if value in (None, "", False):
        return Decimal("0")
    return Decimal(str(value))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def entry_date_key(item: dict) -> str | None:
    ts = item.get("uTime") or item.get("cTime") or item.get("ts")
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(str(ts)) / 1000, TZ)
    except Exception:
        return None
    return dt.strftime("%Y-%m-%d")


def history_entry_key(item: dict) -> str:
    return "|".join(
        [
            str(item.get("posId") or ""),
            str(item.get("instId") or ""),
            str(item.get("uTime") or item.get("cTime") or item.get("ts") or ""),
            str(item.get("realizedPnl") or "0"),
            str(item.get("closeAvgPx") or ""),
        ]
    )


def build_snapshot() -> Snapshot:
    balance = run_okx("account", "balance")
    positions = run_okx("account", "positions", "--instType", "SWAP")
    history = run_okx("account", "positions-history", "--instType", "SWAP", "--limit", "100")

    row = balance[0] if isinstance(balance, list) and balance else {}
    details = row.get("details") or []
    usdt_detail = next((d for d in details if d.get("ccy") == "USDT"), {})

    contract_unrealized = Decimal("0")
    normalized_positions: list[dict] = []
    if isinstance(positions, list):
        for pos in positions:
            upl = to_decimal(pos.get("upl"))
            contract_unrealized += upl
            normalized_positions.append(
                {
                    "instId": pos.get("instId"),
                    "side": pos.get("posSide") or pos.get("side") or "net",
                    "pos": pos.get("pos") or pos.get("size") or pos.get("sz") or "0",
                    "avgPx": pos.get("avgPx") or pos.get("openAvgPx") or "-",
                    "upl": str(upl),
                    "lever": pos.get("lever") or "-",
                }
            )

    spot_non_usdt_value = Decimal("0")
    spot_assets: list[dict] = []
    for d in details:
        ccy = str(d.get("ccy") or "")
        eq_usd = to_decimal(d.get("eqUsd"))
        eq = to_decimal(d.get("eq"))
        if ccy and ccy != "USDT" and eq != 0:
            spot_non_usdt_value += eq_usd
            spot_assets.append({"ccy": ccy, "eq": str(eq), "eqUsd": str(eq_usd)})

    return Snapshot(
        total_eq=to_decimal(row.get("totalEq")),
        usdt_eq=to_decimal(usdt_detail.get("eq")),
        usdt_avail=to_decimal(usdt_detail.get("availBal")),
        contract_unrealized_pnl=contract_unrealized,
        positions=normalized_positions,
        history_entries=history if isinstance(history, list) else [],
        spot_non_usdt_value=spot_non_usdt_value,
        spot_assets=spot_assets,
    )


def fmt(d: Decimal) -> str:
    q = d.quantize(Decimal("0.01"))
    sign = "+" if q > 0 else ""
    return f"{sign}{q}"


def update_state_from_snapshot(state: dict, snapshot: Snapshot) -> tuple[Decimal, Decimal]:
    now = datetime.now(TZ)
    date_key = now.strftime("%Y-%m-%d")

    if "spot_tracking_start_value" not in state:
        state["spot_tracking_start_value"] = str(snapshot.spot_non_usdt_value)
        state["tracking_start_at"] = now.isoformat()

    spot_day_start = state.setdefault("spot_day_start_value", {})
    if date_key not in spot_day_start:
        spot_day_start[date_key] = str(snapshot.spot_non_usdt_value)

    seen_keys = set(state.get("contract_realized_seen_keys", []))
    tracking_realized = to_decimal(state.get("contract_realized_tracking", "0"))
    daily_realized = state.setdefault("contract_realized_by_date", {})

    for item in snapshot.history_entries:
        key = history_entry_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        realized = to_decimal(item.get("realizedPnl"))
        tracking_realized += realized
        dkey = entry_date_key(item)
        if dkey:
            daily_realized[dkey] = str(to_decimal(daily_realized.get(dkey, "0")) + realized)

    # Keep bounded state size.
    state["contract_realized_seen_keys"] = list(sorted(seen_keys))[-1000:]
    state["contract_realized_tracking"] = str(tracking_realized)

    return tracking_realized, to_decimal(daily_realized.get(date_key, "0"))


def render(snapshot: Snapshot, state: dict) -> str:
    now = datetime.now(TZ)
    date_key = now.strftime("%Y-%m-%d")
    profile = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["okx"]["profile"]

    tracking_realized, today_realized = update_state_from_snapshot(state, snapshot)
    today_contract_pnl = today_realized + snapshot.contract_unrealized_pnl
    tracking_contract_pnl = tracking_realized + snapshot.contract_unrealized_pnl

    spot_tracking_start = to_decimal(state["spot_tracking_start_value"])
    spot_day_start = to_decimal(state["spot_day_start_value"][date_key])
    spot_day_delta = snapshot.spot_non_usdt_value - spot_day_start
    spot_tracking_delta = snapshot.spot_non_usdt_value - spot_tracking_start

    lines = [
        f"【TradeClaw 整点报告】{now.strftime('%Y-%m-%d %H:%M')}（{date_key}）",
        f"模式：{profile}",
        f"合约浮盈亏：{fmt(snapshot.contract_unrealized_pnl)} USDT",
        f"合约已实现盈亏（追踪以来）：{fmt(tracking_realized)} USDT",
        f"今日合约盈亏：{fmt(today_contract_pnl)} USDT",
        f"现货资产波动（今日）：{fmt(spot_day_delta)} USDT",
        f"现货资产波动（追踪以来）：{fmt(spot_tracking_delta)} USDT",
        f"账户总权益：{snapshot.total_eq.quantize(Decimal('0.01'))} USDT",
        f"USDT 余额：{snapshot.usdt_eq.quantize(Decimal('0.01'))} / 可用 {snapshot.usdt_avail.quantize(Decimal('0.01'))}",
    ]

    if snapshot.positions:
        lines.append("当前合约仓位：")
        for pos in snapshot.positions:
            lines.append(
                f"- {pos['instId']} | {pos['side']} | 仓位 {pos['pos']} | 均价 {pos['avgPx']} | 浮盈亏 {fmt(to_decimal(pos['upl']))} | 杠杆 {pos['lever']}x"
            )
    else:
        lines.append("当前合约仓位：无")

    return "\n".join(lines)


def main() -> int:
    state = load_state()
    snapshot = build_snapshot()
    text = render(snapshot, state)
    save_state(state)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
