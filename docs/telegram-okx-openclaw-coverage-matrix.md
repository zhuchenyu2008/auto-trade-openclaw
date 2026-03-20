# Telegram OKX OpenClaw Coverage Matrix

Version: M0 repo-local baseline  
Date: 2026-03-19

## 1. Status Legend

- `implemented`: current repo has code plus repo-local test hooks
- `partial`: code exists but coverage or behavior is intentionally limited
- `planned`: covered by docs only; next milestone must add fixtures or scripts

## 2. Matrix

| Req ID | Spec requirement summary | Current implementation surface | Status | Repo-local verification assets | Planned doc/case refs | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `RQ-01` | public Telegram ingestion through `public_web` | `src/tg_okx_auto_trade/telegram.py`, `config.demo.local.json` | implemented | `tests.test_app.test_public_web_*` | `TC-PW-001` to `TC-PW-003` | primary M0 source path |
| `RQ-02` | detect new messages and best-effort edits | `parse_public_channel_html`, `_normalize_public_web_post` | implemented | `test_public_web_html_parses_into_normalized_messages_and_detects_edits`, `test_telegram_watcher_increments_version_for_multiple_edits` | `TC-PW-001`, `TC-DED-002` | edit detection is semantic-hash based |
| `RQ-03` | 30-second reconciliation path | `Runtime._reconcile_loop`, `Runtime.reconcile_now`, `TelegramWatcher.reconcile_once` | partial | `test_reconcile_counts_each_replayed_buffered_message`, `test_reconcile_now_surfaces_failure_without_raising`, `scripts/smoke_telegram.py` | `TC-REC-001`, `TC-REC-002` | Bot API reconcile only replays in-process buffer |
| `RQ-04` | normalize, version, and dedup messages | `NormalizedMessage`, `Storage.save_message`, `RiskEngine._idempotency_key` | implemented | duplicate/edit/version tests in `tests/test_app.py` | `TC-DED-001`, `TC-DED-002` | fixture chains still needed |
| `RQ-05` | independent OpenClaw AI agent path | `src/tg_okx_auto_trade/ai.py`, `config.demo.local.json: ai.openclaw_agent_id=tgokxai` | partial | wrapper and fallback unit tests | `TC-AI-001` to `TC-AI-003` | repo-local tests mostly use heuristic for determinism |
| `RQ-06` | contracts-only, default leverage 20x | `config.py`, `risk.py`, `okx.py` | implemented | leverage and validation unit tests | `TC-RISK-001`, `TC-RISK-003` | live mode rejected in config validation |
| `RQ-07` | global TP/SL optional, default off | `TradingConfig`, `Runtime._apply_global_protection` | implemented | `test_global_tp_sl_disabled_by_default`, `test_global_tp_sl_enabled_applies_protection_to_new_position` | `TC-RISK-002` | configured OKX REST still partial for ratio-based protection |
| `RQ-08` | demo-only execution, never live | `validate_config`, `RiskEngine.evaluate`, `OKXGateway.execute` | implemented | live-disabled and manual inject tests | `TC-OKX-001`, `TC-OKX-002` | release gate must keep this locked |
| `RQ-09` | OKX demo action coverage for open/add/reduce/reverse/close/cancel | `OKXGateway`, `REAL_DEMO_SUPPORTED_ACTIONS` | partial | real-demo mapping tests, `scripts/smoke_okx_demo.py` | `TC-OKX-002` to `TC-OKX-004` | `update_protection`, trailing, ratio TP/SL remain simulated-only |
| `RQ-10` | auto-pause on execution failure | `Runtime._run_pipeline`, `pause_trading` | implemented | `test_execution_failure_auto_pauses_trading` | `TC-OKX-005` | critical runtime guard |
| `RQ-11` | topic logging outbound | `topic_logger.py`, `Runtime.send_topic_test` | partial | topic normalization/disable/failure unit tests, `scripts/smoke_web.py` | `TC-TOP-001`, `TC-TOP-002` | real send depends on `openclaw` CLI and network |
| `RQ-12` | small Claw operator surface | `Runtime.run_operator_command`, watcher operator routing | implemented | `scripts/smoke_operator.py`, operator unit tests | `TC-TOP-003` | intended path is outbound topic logs plus Web/local commands; inbound bot path is legacy/internal only |
| `RQ-13` | Web control panel with 6-digit PIN | `web.py`, `Runtime.authenticate` | implemented | web login/unit tests, `scripts/smoke_web.py`, `scripts/smoke_http_server.py` | `TC-WEB-001` to `TC-WEB-003` | browser automation itself is not in M0 |
| `RQ-14` | config file as first-class control surface | `config.py`, `Runtime.update_config`, channel helpers | implemented | config helper tests, CLI smoke, runtime hot-reload tests | `TC-WEB-002`, `TC-RECOV-001`, `TC-RECOV-002` | repo supports file, Web, and operator-command paths |
| `RQ-15` | runtime artifacts and readiness outputs | `Runtime.public_snapshot`, `public_verification_report`, `direct_use_payload`, `usage_paths` | implemented | artifact unit tests, `scripts/smoke_runtime.py` | `TC-RECOV-003` | `scripts/verify_demo.py` needs assertion refresh |
| `RQ-16` | delete/revoke handling if source supports it | stored config only; no active implementation | partial | `test_readiness_and_remaining_gaps_warn_when_delete_events_are_requested` | n/a | explicitly non-gating for M0 |
| `RQ-17` | MTProto/private channel expansion | config validation can store `mtproto`; no active watcher | partial | `test_readiness_warns_for_mtproto_channels` | n/a | explicitly out of scope |
| `RQ-18` | fixture-backed parser and replay corpus of 120+ samples | not present yet | planned | none yet | `TC-FXT-001` to `TC-FXT-003`, fixture spec | required for next milestone |

## 3. Repo Drift Notes

Important differences between the external high-level test docs and the current repo:

- the repo still contains `bot_api` smoke coverage, but this doc set narrows release scope to `public_web`
- the repo now exposes more explicit Web runtime actions than the older external test docs assumed
- `scripts/verify_demo.py` currently lags the runtime readiness model and should not be used as a final gate without refresh
- one unit test around secret externalization is currently failing, so the baseline is not fully green
