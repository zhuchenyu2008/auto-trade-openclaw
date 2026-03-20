# Telegram OKX OpenClaw Test Cases

Version: M0 repo-local baseline  
Date: 2026-03-19

## 1. How To Use This File

- `Current hook` means a command or test already present in this repo.
- `Planned hook` means the case is defined now but needs the next fixture milestone to become automatable.
- Evidence must be saved as terminal output, JSON output, or runtime artifact output.

## 2. Executable Cases

| ID | Area | Prerequisites | Steps | Expected result | Evidence | Current hook |
| --- | --- | --- | --- | --- | --- | --- |
| `TC-PW-001` | public_web parse new + edit | none | Run `python3 -m unittest tests.test_app.AppTests.test_public_web_html_parses_into_normalized_messages_and_detects_edits` | HTML post parses into `public_web` normalized messages; first version is `new`; changed post becomes `edit` with incremented version | unittest pass output | existing |
| `TC-PW-002` | public_web readiness without bot token | none | Run `python3 -m unittest tests.test_app.AppTests.test_public_web_readiness_allows_automatic_ingestion_without_bot_token` | readiness treats enabled `public_web` channels as sufficient for automatic ingestion; inbound operator commands still blocked without bot token | unittest pass output | existing |
| `TC-PW-003` | watcher health on public_web-only config | none | Run `python3 -m unittest tests.test_app.AppTests.test_telegram_watcher_reports_connected_health_for_public_web_without_bot_token` | watcher health reports connected/configured state for `public_web` channels | unittest pass output | existing |
| `TC-PAR-001` | parser hashtag symbol extraction | none | Run `python3 -m unittest tests.test_app.AppTests.test_heuristic_parser_extracts_symbol_from_public_web_hashtag_signal` | `#TOKEN` public-web text resolves to `TOKEN-USDT-SWAP` | unittest pass output | existing |
| `TC-PAR-002` | parser ignore noise / TP broadcast | none | Run `python3 -m unittest tests.test_app.AppTests.test_heuristic_parser_ignores_take_profit_broadcast_without_fresh_entry` | non-entry broadcast is mapped to `ignore`; no executable trade is created | unittest pass output | existing |
| `TC-PAR-003` | parser ignore ambiguous chatter | none | Run `python3 -m unittest tests.test_app.AppTests.test_heuristic_parser_ignores_ambiguous_chatter_without_symbol` | chatter without a resolvable symbol is `ignore` and does not open a trade | unittest pass output | existing |
| `TC-AI-001` | OpenClaw wrapper nested payload extraction | none | Run `python3 -m unittest tests.test_app.AppTests.test_run_openclaw_extracts_text_from_wrapped_json_payloads` | OpenClaw wrapper can read model text from wrapped JSON payloads | unittest pass output | existing |
| `TC-AI-002` | OpenClaw fallback metadata | none | Run `python3 -m unittest tests.test_app.AppTests.test_non_heuristic_provider_records_fallback_metadata_when_ai_call_fails tests.test_app.AppTests.test_runtime_health_exposes_ai_fallback_reason` | provider failure falls back to heuristic and exposes fallback metadata in health/raw payload | unittest pass output | existing |
| `TC-AI-003` | OpenClaw output normalization | none | Run `python3 -m unittest tests.test_app.AppTests.test_openclaw_payload_defaults_missing_fields_and_normalizes_symbol tests.test_app.AppTests.test_openclaw_aliases_close_to_close_all tests.test_app.AppTests.test_openclaw_aliases_cancel_entry_to_cancel_orders` | missing defaults are filled, symbol normalization is stable, close/cancel aliases land on repo action names | unittest pass output | existing |
| `TC-DED-001` | duplicate message version suppression | none | Run `python3 -m unittest tests.test_app.AppTests.test_duplicate_message_version_does_not_duplicate_order` | same `(chat_id, message_id, version)` does not create a second order | unittest pass output | existing |
| `TC-DED-002` | edit version re-executes with new idempotency key | none | Run `python3 -m unittest tests.test_app.AppTests.test_edit_creates_new_version_without_duplicate_block tests.test_app.AppTests.test_telegram_watcher_increments_version_for_multiple_edits` | edited message versions are distinct and can drive new execution | unittest pass output | existing |
| `TC-REC-001` | reconcile replays buffered Telegram messages | none | Run `python3 -m unittest tests.test_app.AppTests.test_reconcile_counts_each_replayed_buffered_message` | reconcile reports replay count and updates last reconcile status | unittest pass output | existing |
| `TC-REC-002` | reconcile failure surfaces warn instead of crashing | none | Run `python3 -m unittest tests.test_app.AppTests.test_reconcile_now_surfaces_failure_without_raising` | reconcile returns warning summary; runtime health shows warn | unittest pass output | existing |
| `TC-RISK-001` | default leverage 20x | none | Run `python3 -m unittest tests.test_app.AppTests.test_default_leverage_is_20` | default leverage on new orders is `20` | unittest pass output | existing |
| `TC-RISK-002` | global TP/SL default off and opt-in | none | Run `python3 -m unittest tests.test_app.AppTests.test_global_tp_sl_disabled_by_default tests.test_app.AppTests.test_global_tp_sl_enabled_applies_protection_to_new_position` | default-off stays off; opt-in adds ratio-based protection on eligible actions | unittest pass output | existing |
| `TC-RISK-003` | close-only protection | none | Run `python3 -m unittest tests.test_app.AppTests.test_close_only_rejects_open_signal` | close-only mode rejects open signals | unittest pass output | existing |
| `TC-RISK-004` | invalid size and invalid side rejection | none | Run `python3 -m unittest tests.test_app.AppTests.test_invalid_intent_size_is_rejected tests.test_app.AppTests.test_invalid_intent_side_is_rejected` | invalid intent shape is rejected before execution | unittest pass output | existing |
| `TC-OKX-001` | manual inject defaults to simulated path | none | Run `python3 -m unittest tests.test_app.AppTests.test_manual_inject_defaults_to_simulated_even_when_okx_demo_is_configured` | manual inject uses simulated demo path even if configured OKX demo is enabled | unittest pass output | existing |
| `TC-OKX-002` | configured demo path opt-in | none | Run `python3 -m unittest tests.test_app.AppTests.test_manual_inject_can_opt_into_real_okx_demo_path` | `use_configured_okx_path` flips manual inject to configured OKX demo path | unittest pass output | existing |
| `TC-OKX-003` | configured demo cancel path | none | Run `python3 -m unittest tests.test_app.AppTests.test_manual_inject_cancel_orders_can_use_real_okx_demo_path` | cancel action can use configured demo path for supported order state | unittest pass output | existing |
| `TC-OKX-004` | reverse path mapping | none | Run `python3 -m unittest tests.test_app.AppTests.test_real_demo_reverse_path_closes_then_reopens_to_target_side tests.test_app.AppTests.test_reverse_signal_switches_demo_position_side` | reverse updates local expected position correctly on simulated and configured demo paths | unittest pass output | existing |
| `TC-OKX-005` | execution failure auto-pauses runtime | none | Run `python3 -m unittest tests.test_app.AppTests.test_execution_failure_auto_pauses_trading` | execution failure pauses trading and records reason | unittest pass output | existing |
| `TC-TOP-001` | topic target normalization | none | Run `python3 -m unittest tests.test_app.AppTests.test_operator_topic_link_is_normalized_in_runtime_wiring tests.test_app.AppTests.test_report_topic_link_is_used_when_operator_target_missing` | topic links normalize to internal `-100...:topic:...` form and thread id is aligned | unittest pass output | existing |
| `TC-TOP-002` | topic delivery disabled smoke | none | Run `python3 -m unittest tests.test_app.AppTests.test_topic_send_can_be_disabled_for_safe_smoke_runs tests.test_app.AppTests.test_capability_summary_marks_topic_delivery_disabled_when_env_requests_it` | topic smoke is disabled safely by `TG_OKX_DISABLE_TOPIC_SEND=1` and status surfaces that clearly | unittest pass output | existing |
| `TC-TOP-003` | operator commands | none | Run `python3 scripts/smoke_operator.py --config config.demo.local.json` | help/status/readiness/paths/channels/signals/risk/positions/orders/pause/resume/reconcile/topic-test/close flows are handled | JSON smoke output | existing |
| `TC-WEB-001` | Web login and state fetch | none | Run `python3 -m unittest tests.test_app.AppTests.test_web_login_and_api_smoke` | login accepts valid six-digit PIN, API state requires auth, state payload is redacted | unittest pass output | existing |
| `TC-WEB-002` | Web config save and runtime actions | none | Run `python3 scripts/smoke_web.py --config config.demo.local.json` | AI config save, inject, channel upsert/toggle/remove, reconcile, topic-test, close, and reset-local-state endpoints work through Web controller | JSON smoke output | existing |
| `TC-WEB-003` | HTTP server smoke | local port bind allowed | Run `python3 scripts/smoke_http_server.py --config config.demo.local.json` | real local HTTP server answers `/healthz`, `/readyz`, login, and authenticated state routes | JSON smoke output or `skipped` when sandbox blocks bind | existing |
| `TC-RECOV-001` | config hot reload | none | Run `python3 -m unittest tests.test_app.AppTests.test_runtime_hot_reloads_external_config_change` | runtime reloads external config file edits without restart for supported fields | unittest pass output | existing |
| `TC-RECOV-002` | local `.env` hot reload | none | Run `python3 -m unittest tests.test_app.AppTests.test_config_manager_reload_detects_local_env_bot_token_change tests.test_app.AppTests.test_config_manager_reload_detects_local_env_okx_credential_change` | runtime picks up local `.env` changes for bot token and OKX demo credentials | unittest pass output | existing |
| `TC-RECOV-003` | simulated position restore and runtime artifacts | none | Run `python3 -m unittest tests.test_app.AppTests.test_simulated_positions_are_restored_after_restart tests.test_app.AppTests.test_runtime_writes_public_runtime_artifacts` | positions restore after restart; direct-use and public-state artifacts are written | unittest pass output | existing |

## 3. Fixture-Driven Cases

| ID | Area | Prerequisites | Steps | Expected result | Evidence | Planned hook |
| --- | --- | --- | --- | --- | --- | --- |
| `TC-FXT-001` | 120-sample parser corpus | none | Run `python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/messages` | all corpus items produce the expected parser contract or expected ignore/reject result | fixture suite JSON report with per-fixture ids | existing |
| `TC-FXT-002` | replay, reconcile, and dedup chains | none | Run `python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/scenarios` | duplicate same-version events are suppressed, edit chains increment version, replay chains reconcile correctly | fixture suite JSON report plus scenario chain ids | existing |
| `TC-FXT-003` | public_web HTML corpus | none | Run `python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/html` | HTML pages/fragments normalize to the expected public-web post records | fixture suite JSON report plus stored normalized output | existing |

## 4. Current Repo-Local Execution Notes

- `scripts/verify_demo.py` is a stable repo-local gate again and now expects `config.demo.local.json` to report `current_operating_profile=ready` and `automatic_telegram=ready` because the checked config uses enabled `public_web` channels.
- The offline/local smoke scripts that exercise reconcile, Web, runtime, and operator flows use channel-less temp clones so those cases stay deterministic and do not depend on live `public_web` fetches.
- `scripts/smoke_http_server.py` is non-gating when sandbox policy blocks local socket bind.
- `scripts/smoke_okx_demo.py` is non-gating for `M2` when outbound network or DNS is unavailable; credentialed OKX demo validation remains part of `M3`.
