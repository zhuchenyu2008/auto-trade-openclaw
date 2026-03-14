---
name: tg-okx-tradeclaw-ops
description: "Operate, deploy, configure, and troubleshoot the TradeClaw Telegram→OpenClaw→OKX trading system living at `/root/.openclaw/workspace/projects/tg-okx-trade-claw`. Use when the user asks to: (1) explain how this trading Claw works, (2) check whether it is running or why it did/did not trade, (3) change leverage, position sizing, report format, source channel, Telegram topic target, demo/live profile, or other project config, (4) reset runtime state and restart from ‘now’, (5) inspect OKX balances/positions/orders for this project, (6) update the hourly report, systemd service, or topic behavior for this trading topic."
---

# TradeClaw Ops

Operate the existing TradeClaw project in `/root/.openclaw/workspace/projects/tg-okx-trade-claw`.

## Start here

Before answering or changing anything about this system, read:

1. `/root/.openclaw/workspace/projects/tg-okx-trade-claw/README.md`
2. `/root/.openclaw/workspace/projects/tg-okx-trade-claw/config.json`

Then choose the path below.

## Workflow

### 1. Explain or answer status questions

When the user asks things like:
- “现在什么状态”
- “为什么没下单”
- “现在杠杆是多少”
- “现在用什么开仓”
- “模拟盘里还有多少 USDT”

Do not guess. Check live state first.

Use the commands and checks in `references/operations.md`.

Default checks:
- `systemctl status tradeclaw.service`
- `journalctl -u tradeclaw.service`
- project `state/*`
- `okx --profile <demo|live> ...`

Answer briefly: conclusion first, then one short reason.

### 2. Change TradeClaw behavior

Use this skill when the user wants to change:
- leverage
- position size bounds
- source channel
- report target/topic
- dry-run vs execution mode
- demo vs live profile
- hourly report format
- startup/reset behavior
- topic/system prompt behavior for the trading topic

Edit the minimal necessary files only.

Typical files:
- project config: `/root/.openclaw/workspace/projects/tg-okx-trade-claw/config.json`
- project code: `/root/.openclaw/workspace/projects/tg-okx-trade-claw/**`
- service: `/etc/systemd/system/tradeclaw.service`
- OpenClaw topic routing/prompt: `/root/.openclaw/openclaw.json`

After edits:
1. run `openclaw doctor --non-interactive` if OpenClaw config/service was changed
2. restart/reload only what is needed
3. verify with a real status check
4. report the final state in plain Chinese

### 3. Reset runtime state and restart from “now”

When the user wants to “清空数据 / 从现在开始重新跑 / 不吃历史消息”:
- stop the service
- clear only runtime state and audit files
- keep code/config intact
- restart service
- verify the watcher rebuilt its baseline without replaying old signals

Use the reset notes in `references/operations.md`.

### 4. Inspect OKX account impact

When the user asks about:
- available USDT
- whether a position exists
- whether TradeClaw really placed an order
- why total equity moved
- whether spot holdings are polluting the report

Check:
- account balance
- swap positions
- open orders
- algo orders
- project audit file (if present)

If spot holdings distort the report, say so clearly. If the user asks to convert spot assets to USDT, treat that as a real trading action and use the OKX CLI carefully.

### 5. Update the hourly report

The hourly report script is:
- `/root/.openclaw/workspace/projects/tg-okx-trade-claw/scripts/hourly_report.py`

When changing report format:
- prefer a trading-account view over a generic equity view
- keep output concise and readable in Telegram
- separate contract PnL from spot asset effects
- test the script locally after edits

### 6. Topic behavior

This trading topic is meant to be conversational, not log-only.

If the user asks for “直接在 topic 里和小Claw说”, update the Telegram topic config in OpenClaw so the topic:
- does not require mention
- knows it is the TradeClaw operator for this project
- can answer questions and apply config changes
- stays scoped to this trading system only

## Rules

- Treat this as an **event-driven** trading system. Do not turn it into a self-directed market agent unless the user explicitly asks.
- Do not invent state; always inspect first.
- Prefer minimal edits.
- After changing OpenClaw config or service behavior, run `openclaw doctor --non-interactive` before restart/reload.
- When editing runtime behavior, keep the user’s actual trading boundaries intact: leverage fixed externally; sizing/reporting/profile should match current config unless explicitly changed.

## References

- Use `references/operations.md` for common commands and restart/check flows.
- Use `references/config-map.md` for important config knobs and what they mean.
