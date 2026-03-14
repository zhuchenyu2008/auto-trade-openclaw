# TradeClaw operations

Project root:
- `/root/.openclaw/workspace/projects/tg-okx-trade-claw`

## Read first

- `README.md`
- `config.json`

## Service status

```bash
systemctl --no-pager --full status tradeclaw.service
journalctl -u tradeclaw.service -n 100 --no-pager
```

## Project runtime state

```bash
ls -l /root/.openclaw/workspace/projects/tg-okx-trade-claw/state
sed -n '1,200p' /root/.openclaw/workspace/projects/tg-okx-trade-claw/state/state.json
sed -n '1,200p' /root/.openclaw/workspace/projects/tg-okx-trade-claw/state/hourly_report_state.json
```

## OKX account checks

Use the profile from `config.json`.

```bash
okx --profile demo account balance --json
okx --profile demo account balance USDT --json
okx --profile demo account positions --instType SWAP --json
okx --profile demo swap orders --json
okx --profile demo swap algo orders --json
okx --profile demo account positions-history --instType SWAP --limit 20 --json
```

Replace `demo` with `live` if the project is configured that way.

## One-shot local checks

```bash
cd /root/.openclaw/workspace/projects/tg-okx-trade-claw
PYTHONPATH=$PWD python3 -m tradeclaw.cli --config config.json --once
python3 scripts/hourly_report.py
PYTHONPATH=$PWD python3 -m unittest discover -s tests -v
```

## Reset runtime state from “now”

Use when the user wants to clear local data but keep code/config.

```bash
cd /root/.openclaw/workspace/projects/tg-okx-trade-claw
systemctl stop tradeclaw.service
rm -f state/okx_audit.jsonl state/hourly_report_state.json
cat > state/state.json <<'EOF'
{
  "channels": {}
}
EOF
systemctl start tradeclaw.service
```

Expected effect:
- old runtime/audit/report baseline is cleared
- current channel content is re-learned as baseline
- old messages are not replayed as new trades

## OpenClaw topic changes

If you change `/root/.openclaw/openclaw.json` or topic behavior:

```bash
openclaw doctor --non-interactive
openclaw gateway restart
openclaw gateway status
```

For Telegram topic smoke message:

```bash
openclaw message send --channel telegram --target <group-id> --thread-id <topic-id> --message 'TradeClaw config updated.' --json
```

## Spot cleanup into USDT

Use only when explicitly requested by the user.

1. inspect current balance
2. identify non-USDT assets
3. place market spot sells carefully
4. re-check balance

Examples:

```bash
okx --profile demo spot place --instId BTC-USDT --side sell --ordType market --sz 1 --tdMode cash --json
okx --profile demo spot place --instId ETH-USDT --side sell --ordType market --sz 1 --tdMode cash --json
okx --profile demo spot place --instId OKB-USDT --side sell --ordType market --sz 100 --tdMode cash --json
```

## Audit trail

Execution audit file when present:
- `/root/.openclaw/workspace/projects/tg-okx-trade-claw/state/okx_audit.jsonl`

If it is empty or missing, say so explicitly rather than implying a trade happened.
