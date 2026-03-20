# tg-okx-auto-trade

Minimal-dependency implementation of the Telegram signal watcher + OpenClaw intent extraction + OKX contracts demo execution system described in the project spec.

This build is intentionally scoped for direct use in this environment:

- contracts only: swap/futures
- default leverage: `20x`
- web panel default port: `6010`
- global TP/SL exists and is disabled by default
- config file is a first-class control surface alongside Web and Telegram topic output
- demo/simulated trading only by default; no live trading tests are performed
- demo-only enforcement is hard-blocked in config validation and risk checks
- no secrets are stored in example files

## What is implemented

- Telegram Bot API watcher with new/edit event handling and reconciliation hook
- delete/revoke handling is not implemented in this dependency-light Bot API build; `listen_deletes` remains a stored config flag only
- message normalization, versioning, idempotency, AI parsing, risk checks, execution pipeline
- OpenClaw CLI adapter using the local default model path, with heuristic fallback
- OKX execution gateway:
  - simulated demo engine for safe local operation
  - real OKX REST demo request path available when credentials are configured
- topic logger via `openclaw message send`
- operator topic command handling for `/help`, `/status`, `/readiness`, `/paths`, `/channels`, `/signals`, `/risk`, `/positions`, `/orders`, `/pause`, `/resume`, `/reconcile`, `/close`, and `/topic-test` when the bot can receive topic messages
- SQLite persistence for channels, messages, AI decisions, risk checks, orders, positions, logs, audit logs, sessions
- authenticated web UI with 6-digit PIN, dashboard, AI config controls, trading controls, risk controls, channel management, logs, orders, positions, health
- direct-use summary exposed consistently in CLI, runtime artifacts, and the Web dashboard
- explicit capability/status view for manual demo path, OKX demo execution path, Telegram ingestion readiness, operator topic wiring, and demo-only safety lock
- explicit web operator actions for pause, resume, reconcile-now, topic smoke test, and manual position close
- observe-only execution path that records intended trades without touching OKX state
- automatic demo-only safety pause when OKX execution fails
- config loader and write-back through `config.json`
- unit tests and a smoke verification script

## Project structure

```text
src/tg_okx_auto_trade/
  ai.py
  config.py
  main.py
  models.py
  okx.py
  risk.py
  runtime.py
  storage.py
  telegram.py
  topic_logger.py
  web.py
tests/
scripts/
config.example.json
```

## Quick start

All commands below run directly from the repository root.

Create a local config file with a pinned 6-digit web login:

```bash
python3 -m tg_okx_auto_trade.main init-config --config config.json --pin 123456
```

If you prefer an environment variable instead of a stored hash:

```bash
cp config.example.json config.json
export TG_OKX_WEB_PIN=123456
```

Check readiness and print the exact run paths for the current config:

```bash
python3 -m tg_okx_auto_trade.main verify --config config.json
python3 -m tg_okx_auto_trade.main direct-use --config config.json
```

Start the app:

```bash
python3 -m tg_okx_auto_trade.main serve --config config.json
```

Then open `http://127.0.0.1:6010/login`.

If you want a browser-free smoke check of the startup path:

```bash
python3 -m tg_okx_auto_trade.main serve --config config.json
```

In another shell:

```bash
curl -i http://127.0.0.1:6010/login
curl -s http://127.0.0.1:6010/healthz
curl -s http://127.0.0.1:6010/readyz
```

For the checked-in demo wiring file in this workspace:

```bash
python3 -m tg_okx_auto_trade.main paths --config config.demo.local.json
python3 -m tg_okx_auto_trade.main verify --config config.demo.local.json
python3 -m tg_okx_auto_trade.main direct-use --config config.demo.local.json
python3 -m tg_okx_auto_trade.main externalize-secrets --config config.demo.local.json
python3 -m tg_okx_auto_trade.main serve --config config.demo.local.json
```

This local demo config is prewired for:

- Web on `http://127.0.0.1:6010/login`
- runtime state under `runtime/demo-local/`
- runtime direct-use files at `runtime/demo-local/direct-use.json`, `runtime/demo-local/direct-use.txt`, and `runtime/demo-local/public-state.json`
- operator topic target `-1003720752566:topic:2080`
- operator topic link `https://t.me/c/3720752566/2080`
- demo-only trading guardrails

This local demo profile is ready for Web control, config persistence, runtime artifacts, manual demo injection, and the configured OKX demo REST path. Automatic source-channel ingestion can now run either through a Telegram bot token + enabled `bot_api` source channel, or through an enabled public-channel webpage source using `source_type="public_web"` with `channel_username` set to a public Telegram channel.

The bundled local Web PIN is `123456`.

`config.demo.local.json`, `.env`, and `runtime/` are kept local-only and are ignored by git in this workspace so demo credentials and runtime state do not get swept into normal diffs.
If OKX demo or Telegram secrets are still embedded inline in `config.demo.local.json`, prefer `python3 -m tg_okx_auto_trade.main externalize-secrets --config config.demo.local.json` so the local `.env` holds the secrets and the config file stays redacted.

Because `config.demo.local.json` has `okx.enabled=true`, automatic Telegram-driven execution will use the real OKX demo REST path. Manual `inject-message` and the Web demo-injection form now stay on the local simulated engine by default; opt into credentialed OKX demo execution only when you explicitly pass `--real-okx-demo`, use the Web "configured OKX path" option, or run `scripts/smoke_okx_demo.py`.
In this build, the configured OKX demo REST path covers `open_*`, `add_*`, `reduce_*`, `reverse_*`, `close_all`, and `cancel_orders`. Absolute-price TP/SL values from the source signal are forwarded as `attachAlgoOrds` on supported OKX demo order placements, and configured-path `cancel_orders` reuses the locally tracked attached protection metadata to send OKX demo algo cancels when those ids are available. Ratio-based global TP/SL, trailing protection, and `update_protection` remain simulated-only on the configured path; these gaps are surfaced explicitly in `paths`, `verify`, and the Web dashboard.
Manual close actions now follow the position source: simulated/manual-injected positions close on the simulated path, while credentialed OKX demo positions close on the configured demo REST path.

For a one-command local regression pass that stays demo-only and suppresses outbound topic sends:

```bash
python3 scripts/run_demo_suite.py --config config.demo.local.json
```

## Configuration

An editable install is optional, not required.

1. Set a 6-digit web PIN:
   - easiest: `python3 -m tg_okx_auto_trade.main init-config --config config.json --pin 123456`
   - or export `TG_OKX_WEB_PIN=123456`
   - or set `web.pin_hash` to a SHA256 hash of the PIN
2. Add Telegram channels under `telegram.channels`.
3. For `bot_api` polling, set `telegram.bot_token` or export `TG_OKX_TELEGRAM_BOT_TOKEN`. For public-channel webpage polling, configure an enabled `telegram.channels[]` entry with `source_type="public_web"` and `channel_username` pointing at a public channel like `https://t.me/s/lbeobhpreo`.
4. Leave `okx.enabled=false` unless you explicitly want the real OKX demo REST path.
5. Optionally set `telegram.report_topic` to a Telegram topic target like `-1001234567890:topic:123`. `telegram.operator_target` remains supported and overrides `report_topic` if both are set.
The Web/config path also accepts topic links like `https://t.me/c/3720752566/2080` and normalizes them to the internal target form.
The Telegram wiring form also shows whether the current bot token came from config or env, and it can explicitly clear a stored config token without touching env-based secrets.
6. If `ai.provider="openclaw"`, you can route this project through a dedicated OpenClaw agent by setting `ai.openclaw_agent_id` (for example `tgokxai`). This lets the project use a separate model/provider path from the main assistant session.
7. Keep `trading.position_mode="net"` in this build.

When the HTTP server is already running, changing `web.host` or `web.port` updates the stored config immediately but does not rebind the live listener. The dashboard and `verify` output now show both the active bind and whether a restart is required.

For a real Telegram + OKX demo wiring path, the minimum config is:

- either:
  - `telegram.bot_token` set (or `TG_OKX_TELEGRAM_BOT_TOKEN` exported) plus at least one enabled `telegram.channels[]` entry with `source_type="bot_api"`, or
  - at least one enabled `telegram.channels[]` entry with `source_type="public_web"` and `channel_username` pointing at a public Telegram channel page such as `https://t.me/s/lbeobhpreo`
- `okx.enabled=true`
- `okx.use_demo=true`
- `okx.api_key`, `okx.api_secret`, `okx.passphrase` set for OKX demo, or the env trio `TG_OKX_OKX_API_KEY`, `TG_OKX_OKX_API_SECRET`, `TG_OKX_OKX_PASSPHRASE`

`verify` will warn when `mtproto` channels are configured, because this dependency-light build only actively consumes the Bot API watcher path and the public Telegram webpage watcher path.

The runtime auto-loads a local `.env` next to the config file and the repository-root `.env` as fallback. Existing shell environment variables still win. Local `.env` changes are watched alongside `config.json`, so newly added demo bot tokens or OKX demo credentials can be picked up without persisting them into the config file.
An example variable layout is included in `.env.example`.

Example `bot_api` channel entry:

```json
{
  "id": "vip-btc",
  "name": "VIP BTC",
  "source_type": "bot_api",
  "chat_id": "-1001234567890",
  "channel_username": "",
  "enabled": true,
  "priority": 100,
  "parse_profile_id": "default",
  "strategy_profile_id": "default",
  "risk_profile_id": "default",
  "paper_trading_enabled": true,
  "live_trading_enabled": false,
  "listen_new_messages": true,
  "listen_edits": true,
  "listen_deletes": false,
  "reconcile_interval_seconds": 30,
  "dedup_window_seconds": 3600,
  "notes": ""
}
```

Example `public_web` channel entry:

```json
{
  "id": "koi-public",
  "name": "koi public page",
  "source_type": "public_web",
  "chat_id": "",
  "channel_username": "https://t.me/s/lbeobhpreo",
  "enabled": true,
  "priority": 100,
  "parse_profile_id": "default",
  "strategy_profile_id": "default",
  "risk_profile_id": "default",
  "paper_trading_enabled": true,
  "live_trading_enabled": false,
  "listen_new_messages": true,
  "listen_edits": true,
  "listen_deletes": false,
  "reconcile_interval_seconds": 30,
  "dedup_window_seconds": 3600,
  "notes": "Public Telegram webpage polling"
}
```

The channel editor also accepts:

- `chat_id`: raw `-100...` ids or `https://t.me/c/<chat>/<message>` links
- `channel_username`: `@name`, `https://t.me/name`, or `https://t.me/s/name` for `public_web`

## Inject a local test signal

```bash
python3 -m tg_okx_auto_trade.main inject-message --config config.json --text "LONG BTCUSDT now"
```

This creates a normalized Telegram message, runs AI parsing, runs risk checks, and executes against the simulated OKX demo engine.

If you intentionally want the configured OKX demo REST path instead of the safe simulated path:

```bash
python3 -m tg_okx_auto_trade.main inject-message --config config.demo.local.json --real-okx-demo --text "LONG BTCUSDT now"
```

If OKX replies with `50101` / `APIKey does not match current environment.`, the app now surfaces that as an environment-mismatch hint. In practice, this means the current key is not a demo-environment key for the `x-simulated-trading: 1` path. Use a true OKX demo key/passphrase pair before expecting credentialed demo orders to work.

To simulate an edited Telegram message version:

```bash
python3 -m tg_okx_auto_trade.main inject-message --config config.json --text "SHORT BTCUSDT now" --message-id 101 --event-type edit --version 2
```

For a local read-only state dump without starting background polling:

```bash
python3 -m tg_okx_auto_trade.main paths --config config.json
python3 -m tg_okx_auto_trade.main verify --config config.json
python3 -m tg_okx_auto_trade.main direct-use --config config.json
python3 -m tg_okx_auto_trade.main snapshot --config config.json
```

`paths` prints the shortest direct-use summary: web/runtime/operator topic paths, current wiring, capability status, and the remaining gaps list.
`verify` returns a read-only readiness report with config/auth/storage checks plus the current runtime snapshot.
`direct-use` prints the same redacted terminal-first summary that is written to `runtime/.../direct-use.txt`, and `direct-use --json` emits the structured payload used to build that text view.
Sensitive fields such as bot tokens, OKX credentials, web PIN hashes, and session IDs are redacted from CLI/Web snapshots.
It also reports the normalized operator topic link and a `remaining_gaps` list so you can see, in one place, what is still blocking end-to-end automatic use.
The capability table now includes `current_operating_profile`, which distinguishes "manual/demo direct use is ready" from "full automatic Telegram ingestion is ready".
`verify`, the Web dashboard, and the runtime artifacts also expose an `activation_summary` matrix so you can inspect manual demo, automatic Telegram ingestion, operator-topic outbound/inbound, configured OKX demo, and the demo-only safety lock as separate paths instead of one collapsed status.
`paths` and the Web dashboard now also expose an `activation_checklist` plus placeholder-safe `setup_examples` snippets so the next config edit for operator-topic wiring, source channels, and OKX demo stays explicit.
`paths` now includes the repository root hint, absolute smoke-script commands, and browser-free `curl` commands for `/login`, `/healthz`, and `/readyz`, so you can execute the direct-use flow even when you are not already sitting in the repo root.
The runtime also writes redacted copies of this information into `runtime/.../direct-use.json`, `runtime/.../direct-use.txt`, and `runtime/.../public-state.json` so you can inspect the effective wiring without opening the Web UI.
`direct-use.txt` is the shortest human-readable summary for terminal-first operation; the JSON files keep the structured detail for scripts and review.
Those outputs now also expose `topic_delivery_state`, `topic_delivery_detail`, and `topic_delivery_verified`, so "topic target configured" and "topic smoke already succeeded in this runtime" are visible as different states in CLI/Web/runtime artifacts.
Those runtime artifacts now also carry `verification_status` plus the same actionable `next_steps` list exposed by `verify`, so the file-based/demo path stays directly operable without switching back to the CLI.
For terminal-first setup, you can wire the operator topic and Telegram source channels without editing JSON manually:

```bash
python3 -m tg_okx_auto_trade.main set-topic-target --config config.demo.local.json --target https://t.me/c/3720752566/2080
python3 -m tg_okx_auto_trade.main upsert-channel --config config.demo.local.json --name "VIP BTC" --chat-id -1001234567890
python3 -m tg_okx_auto_trade.main set-channel-enabled --config config.demo.local.json --channel-id vip_btc --disabled
python3 -m tg_okx_auto_trade.main remove-channel --config config.demo.local.json --channel-id vip_btc
```

When no channel exists yet, `paths` now prints `set-channel-enabled` and `remove-channel` helpers with a `<channel-id-from-upsert-channel>` placeholder instead of a misleading fake id.

## Direct-use paths

- CLI bootstrap: `python3 -m tg_okx_auto_trade.main init-config --config config.json --pin 123456`
- CLI paths: `python3 -m tg_okx_auto_trade.main paths --config config.json`
- CLI verify: `python3 -m tg_okx_auto_trade.main verify --config config.json`
- CLI direct-use summary: `python3 -m tg_okx_auto_trade.main direct-use --config config.json`
- CLI snapshot: `python3 -m tg_okx_auto_trade.main snapshot --config config.json`
- CLI serve: `python3 -m tg_okx_auto_trade.main serve --config config.json`
- CLI test signal: `python3 -m tg_okx_auto_trade.main inject-message --config config.json --text "LONG BTCUSDT now"`
- CLI credentialed OKX demo test signal: `python3 -m tg_okx_auto_trade.main inject-message --config config.demo.local.json --real-okx-demo --text "LONG BTCUSDT now"`
- CLI config helpers: `set-topic-target`, `upsert-channel`, `set-channel-enabled`, `remove-channel`
- CLI secret helper: `externalize-secrets` moves inline Telegram/OKX secrets from the config into the local `.env`
- CLI runtime actions: `pause`, `resume`, `reconcile`, `topic-test`, `close-positions`
- CLI local cleanup: `reset-local-state` clears only local runtime DB/log/session state and locally tracked positions; it does not touch any external OKX demo position or order
- CLI operator dry-run: `operator-command --text '/status'`
- Web login: `http://127.0.0.1:6010/login`
- Web runtime controls:
  - AI config: provider, model, thinking level, timeout, system prompt
  - trading mode, pause/resume, leverage, TP/SL, close-only
  - Telegram wiring: bot token, report topic/operator target, operator thread, poll interval
  - channel add, edit, enable/disable, remove with config write-back
  - demo signal injection from the browser with explicit simulated/configured path selection
  - reconcile-now, topic smoke, reset-local-state, and operator-command dry runs
  - manual close-all or per-position close actions
  - recent normalized messages and AI decisions
- Config file remains first-class and is hot-reloaded by the runtime watcher

## Test

Unit tests:

```bash
python3 -m unittest discover -s tests -v
```

Smoke verification:

```bash
python3 scripts/verify_demo.py --config config.demo.local.json
```

This checks the checked-in `config.demo.local.json` wiring and redaction paths, then runs a separate safe local simulated smoke clone so it never places a real OKX demo order during the generic verify step.
It also verifies that the checked-in demo profile writes fresh runtime artifacts under `runtime/demo-local/`, including `direct-use.json`, `direct-use.txt`, and `public-state.json`.

M3 operator prep:

```bash
python3 scripts/m3_acceptance_prep.py --config config.demo.local.json --format markdown > runtime/demo-local/m3-acceptance-prep.md
```

This is a repo-side readiness helper only. It does not perform Telegram or OKX validation. It saves the exact manual `M3` sequence, runtime artifact paths, redacted credential-presence status, evidence checks, and claim boundaries for the main-session credentialed demo run.
The operator handoff is documented in `docs/telegram-okx-openclaw-m3-acceptance-runbook.md`.

Web/controller/runtime smoke:

```bash
python3 scripts/smoke_web.py --config config.demo.local.json
```

This smoke test exercises the login flow and Web actions through the same controller logic used by the HTTP server, without requiring external network access.

Operator command smoke:

```bash
python3 scripts/smoke_operator.py --config config.demo.local.json
```

This verifies the local operator-command path plus watcher routing for topic messages without sending anything to Telegram.

Telegram watcher/reconcile smoke:

```bash
python3 scripts/smoke_telegram.py --config config.demo.local.json
```

This exercises Bot API watcher update handling for new/edit messages plus the buffered reconciliation path, entirely offline.

Config/path normalization smoke:

```bash
python3 scripts/smoke_config.py --config config.demo.local.json
```

This verifies config write-back, topic-link normalization, channel target normalization, and persisted runtime/web paths.

Offline end-to-end smoke:

```bash
python3 scripts/smoke_e2e.py --config config.demo.local.json
```

This runs a local end-to-end demo-only flow: simulated Telegram Bot API update -> runtime pipeline -> authenticated Web state check -> operator command -> manual close.

HTTP server smoke:

```bash
python3 scripts/smoke_http_server.py --config config.demo.local.json
```

This starts the real local HTTP server on a temporary port, exercises `/login`, `/api/state`, demo signal injection, and manual close, then shuts the process down cleanly.

Runtime/config hot-reload smoke:

```bash
python3 scripts/smoke_runtime.py --config config.demo.local.json
```

This clones `config.demo.local.json` into a temporary runtime, verifies direct-use paths, hot-reloads a config edit from disk, runs a simulated demo order, and exercises pause/resume/reconcile without touching live trading.

Credentialed OKX demo smoke:

```bash
python3 scripts/smoke_okx_demo.py --config config.demo.local.json
```

This places a minimal OKX demo order through the real demo REST path, then closes it again. It never touches live trading and suppresses topic logging during the smoke run.

CLI direct-operation smoke:

```bash
python3 scripts/smoke_cli.py --config config.demo.local.json
```

This clones `config.demo.local.json` into a temporary runtime and exercises the direct CLI state, pause/resume, reconcile, topic-test, and close-position paths without touching live trading.

Full local demo regression:

```bash
python3 scripts/run_demo_suite.py --config config.demo.local.json
```

This runs the unit suite plus config/web/runtime/operator/telegram/CLI/E2E/demo smoke steps. The runner now clears any inherited `TG_OKX_DISABLE_TOPIC_SEND` from the parent shell before starting the generic unit and verify steps, then reapplies it only to the smoke steps that should stay offline.
The suite result is explicitly tagged as `demo_only`, and it reports `live_trading_tested: false`.

If the current environment blocks external network access, the OKX demo smoke and real topic smoke can report `skipped` or a network failure even though the local demo/runtime/Web paths are healthy.

## Notes on strict-spec coverage

- Web login is 6-digit PIN-based and persists sessions in SQLite.
- Config changes made in Web write back into `config.json`, and simulated position state is restored from SQLite on restart.
- Global TP/SL fields exist, default to disabled, and are visible/editable in the Web UI.
- When global TP/SL is enabled, new simulated open/add/reverse orders automatically carry those protection settings into stored order/position state.
- Trading is constrained to contract semantics. Live mode and non-demo OKX execution are rejected by config validation and blocked again by the risk/execution layers.
- `observe` / `execution_mode=observe` records the intended order but does not execute it.
- `execution_mode=automatic` is the normal path in this build. `semi_automatic` is intentionally not exposed because there is no approval queue/confirm workflow behind it yet.
- Real OKX demo REST failures now trigger an automatic pause so the runtime stops issuing further orders until the operator fixes config and resumes.
- Real OKX demo REST execution now pre-sets the configured leverage per instrument before submitting the order, which improves first-run demo usability.
- Manual CLI/Web signal injection is safe-by-default: it stays on the simulated engine unless you explicitly opt into the configured OKX demo REST path.
- Topic logging is implemented through OpenClaw Telegram send commands. Configure `telegram.report_topic` or `telegram.operator_target`, and optionally `telegram.operator_thread_id`.
- Operator topic integration now supports outbound logging plus inbound `/status`, `/readiness`, `/paths`, `/channels`, `/signals`, `/risk`, `/positions`, `/orders`, `/pause`, `/resume`, `/reconcile`, `/close`, and `/topic-test` commands when the configured Telegram bot can receive topic messages. This remains partial until the bot is actually wired into the operator topic chat.
- The Web UI now exposes direct-use controls for Telegram wiring, channel lifecycle, demo signal injection, pause/resume/reconcile actions, and recent message/AI inspection.
- Local smoke endpoints are available at `/healthz` and `/readyz` without login for quick verification.
- Telegram `mtproto` channel records are accepted in config but not actively consumed in this zero-dependency build. Bot API is the implemented live watcher path.
- OKX private WebSocket reconciliation is not active in this dependency-light version; simulated execution state is reconciled internally, and a real REST demo submission path is included for credentialed use.
- Simulated execution now covers `reverse_to_long`, `reverse_to_short`, `cancel_orders`, and `update_protection` in addition to open/add/reduce/close flows.

## Self-review against the spec and test plan

Satisfied directly:

- complete project structure
- runnable app
- `config.example.json` and local config loader
- web UI on port `6010`
- Telegram watcher pipeline for Bot API
- OpenClaw orchestration path with fallback
- OKX demo execution path
- topic logger support
- tests and smoke verification
- README run/test instructions

Known gaps:

- MTProto/Telethon watcher is not implemented because the build avoids external packages
- OKX private WebSocket sync is not implemented; the real REST demo path exists, and the simulated engine maintains local execution/position state
- Inbound operator-topic commands are implemented, but they remain partial until the configured Telegram bot is actually present in the operator topic chat and allowed to receive topic messages
