# TradeClaw config map

Main file:
- `/root/.openclaw/workspace/projects/tg-okx-trade-claw/config.json`

## source

### `source.channels`
Telegram public channel usernames to monitor.

### `source.poll_interval_seconds`
HTML polling interval for public channels.

### `source.max_recent_messages`
How many nearby messages to include in context.

## okx

### `okx.profile`
- `demo`
- `live`

Controls which profile the OKX CLI uses.

### `okx.site`
OKX site variant, e.g. `global`.

### `okx.instrument_scope`
Current project uses `swap`.

### `okx.margin_mode`
- `cross`
- `isolated`

### `okx.position_mode`
- `net_mode`
- `long_short_mode`

This affects how orders pass `posSide`.

### `okx.default_leverage`
Global leverage for open/add actions.

### `okx.min_position_pct`
Minimum allowed fraction of available USDT for open/add.

### `okx.max_position_pct`
Maximum allowed fraction of available USDT for open/add.

### `okx.default_position_pct`
Fallback fraction used when the agent does not specify a size.

### `okx.allowed_instruments`
Optional whitelist of tradeable instruments.

## report

### `report.channel`
Usually `telegram`.

### `report.target`
Chat or group id.

### `report.thread_id`
Telegram topic id when posting to a forum topic.

### `report.enabled`
Whether TradeClaw sends operational reports/logs.

### `report.silent`
Telegram silent delivery flag.

## runtime

### `runtime.execution_enabled`
If false, do not execute trades.

### `runtime.dry_run`
If true, plan commands only and do not place real/demo orders.

### `runtime.state_path`
Per-channel seen-state file.

### `runtime.okx_audit_path`
Command audit trail for executed OKX CLI calls.

## openclaw

### `openclaw.session_prefix`
Prefix for synthetic session ids used when calling `openclaw agent`.

### `openclaw.thinking`
Thinking mode passed to OpenClaw agent calls.

### `openclaw.timeout_seconds`
Timeout for each decision turn.

## Other files often edited together

### systemd service
- `/etc/systemd/system/tradeclaw.service`

Typical fields:
- `WorkingDirectory`
- `ExecStart`
- `PYTHONPATH`
- `OPENCLAW_BIN`
- `OKX_BIN`

### hourly report script
- `/root/.openclaw/workspace/projects/tg-okx-trade-claw/scripts/hourly_report.py`

### OpenClaw topic behavior
- `/root/.openclaw/openclaw.json`

Important area:
- `channels.telegram.groups.<groupId>.topics.<topicId>`
