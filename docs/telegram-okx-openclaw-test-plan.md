# Telegram OKX OpenClaw Test Plan

Version: M0 repo-local baseline  
Date: 2026-03-19  
Authoritative product docs reviewed: external spec and external test plans under `/root/.openclaw/workspace/docs/`  
Repo-local truth source for this plan: current code, tests, scripts, and `config.demo.local.json`

## 1. Purpose

This plan turns the external high-level testing intent into a repo-local plan for the current narrowed system:

- public Telegram channel ingestion through `public_web`
- independent OpenClaw AI agent path
- OKX demo only, never live
- topic logging plus the small Claw operator surface
- Web control panel

This document is intentionally honest about what the repo can and cannot verify today.

## 2. Current Repo Reality

What the code currently supports for the scoped system:

- `public_web` polling from `https://t.me/s/<channel>` with new-message detection and best-effort edit detection via semantic hash comparison
- message versioning, deduplication, risk checks, SQLite persistence, runtime artifacts, and manual reconciliation
- OpenClaw CLI integration with explicit `ai.openclaw_agent_id`, plus heuristic fallback when OpenClaw is unavailable or returns unusable output
- demo-only trading guardrails in config validation and risk checks
- simulated OKX execution plus a credentialed OKX demo REST path
- topic logging through `openclaw message send`
- small Claw operator commands for `/help`, `/status`, `/readiness`, `/paths`, `/channels`, `/signals`, `/risk`, `/positions`, `/orders`, `/pause`, `/resume`, `/reconcile`, `/close`, and `/topic-test`
- authenticated Web control panel with login, config edits, channel management, manual inject, reconcile, topic smoke, close, and reset-local-state actions

Known implementation limits that must remain visible in testing docs:

- delete/revoke handling is not implemented
- `mtproto` can be stored in config but is not actively watched
- OKX private WebSocket reconciliation is not implemented
- configured OKX demo REST coverage is partial: `update_protection`, ratio-based global TP/SL, and trailing protection remain simulated-only
- `public_web` edit detection is best-effort; there is no authoritative Telegram edit event stream on that path
- Bot API surfaces still exist in repo tests and internal compatibility code, but they are not part of the intended supported scope

## 3. Scope

### 3.1 In Scope

| Area | In-scope behavior for this plan |
| --- | --- |
| Telegram ingress | `public_web` polling, HTML parsing, normalized message creation, new/edit handling, 30-second reconciliation expectations |
| AI path | independent OpenClaw agent configuration, wrapper behavior, fallback visibility, parser output contract |
| Execution | demo-only simulated path and credentialed OKX demo REST path |
| Risk/runtime | contracts-only validation, default leverage 20x, close-only mode, auto-pause on execution failure, global TP/SL default-off behavior |
| Operator surface | topic target normalization, outbound topic smoke, Web/local small Claw commands, legacy inbound bot path explicitly out of intended scope |
| Web | login, state view, config mutation, channel lifecycle, runtime actions, manual close, reset-local-state |
| Recovery | config hot reload, local `.env` hot reload, restore simulated positions, reconciliation status reporting, runtime artifact generation |

### 3.2 Out of Scope

| Area | Out-of-scope behavior |
| --- | --- |
| Telegram source types | private channels, MTProto/Telethon, user-session scraping |
| Telegram lifecycle | authoritative delete/revoke events |
| Trading mode | live trading, live credential tests, live exchange safety sign-off |
| OKX sync | private WS/account stream parity, full exchange-side reconciliation beyond current REST/local-expected state |
| Browser automation | real browser runs in this milestone |
| Fixture implementation | the 120+ sample corpus and fixture runner are planned here, not delivered in M0 |

## 4. Existing Repo-Local Test Assets

Current executable assets already present in the repo:

- `tests/test_app.py`
  - 118 unit and integration-style tests around runtime, parser behavior, OKX gateway mapping, topic/web surfaces, and CLI helpers
- `scripts/run_demo_suite.py`
  - umbrella runner for unit tests plus smoke scripts
- `scripts/verify_demo.py`
  - direct-use and runtime artifact verification against `config.demo.local.json`
- `scripts/smoke_cli.py`
  - CLI helper and runtime action smoke
- `scripts/smoke_runtime.py`
  - runtime hot-reload, artifact, pause/resume, reconcile smoke
- `scripts/smoke_web.py`
  - Web controller API smoke without a real browser
- `scripts/smoke_operator.py`
  - operator command and watcher routing smoke
- `scripts/smoke_telegram.py`
  - watcher update and reconcile smoke
- `scripts/smoke_http_server.py`
  - real local HTTP server smoke
- `scripts/smoke_okx_demo.py`
  - credentialed OKX demo smoke, with skip behavior when network is blocked
- `scripts/m3_acceptance_prep.py`
  - repo-side `M3` prep helper that prints the manual credentialed-run sequence and evidence checklist without performing external validation

## 5. Test Environments

| Env ID | Purpose | Required setup | Network | Allowed in M0 doc work |
| --- | --- | --- | --- | --- |
| `ENV-LOCAL-OFFLINE` | deterministic repo-local validation | Python, local temp dirs, `TG_OKX_DISABLE_TOPIC_SEND=1`, `ai.provider=heuristic`, OKX disabled or mocked | no outbound dependencies required | yes |
| `ENV-LOCAL-HTTP` | local authenticated HTTP verification without browser automation | `ENV-LOCAL-OFFLINE` plus local port bind ability | local loopback only | yes |
| `ENV-DEMO-CRED` | credentialed demo validation | OpenClaw CLI reachable, OKX demo credentials, optional Telegram bot/topic wiring, outbound network | yes | not executed in this M0 task |
| `ENV-PUBLIC-WEB-LIVE` | real public channel spot checks | network access to `https://t.me/s/<channel>` and configured channel list | yes | not executed in this M0 task |

## 6. Test Phases

| Phase | Goal | Entry criteria | Exit criteria |
| --- | --- | --- | --- |
| `P0` docs baseline | freeze honest scope and define next-milestone test contracts | external docs reviewed, repo inspected | this doc set exists in `docs/`, scope/gaps are explicit |
| `P1` deterministic repo-local | validate parser, dedup, risk, web, operator, recovery without external services | `P0` complete, fixture schema agreed, local commands stable | unit suite green for scoped areas, smoke scripts for offline surfaces green, 120+ fixture corpus added |
| `P2` credentialed demo integration | prove independent AI path and OKX demo path in operator-owned env | `P1` complete, demo creds available, outbound network allowed | public_web ingest, OpenClaw parsing, topic smoke, and OKX demo open/reverse/close all pass |
| `P3` final acceptance | decide whether the repo is directly usable as a demo-only release | `P2` complete, no critical scoped blockers open | acceptance gate in `docs/telegram-okx-openclaw-final-test-plan.md` passes |

## 7. Entry and Exit Criteria by Area

### 7.1 Telegram/Public Web

Entry:

- at least one enabled `public_web` channel exists in test config
- parser expectations for new/edit/noise messages are defined

Exit:

- new-message and edit-message parsing pass on repo-local fixtures
- reconciliation behavior is verified and documented as best-effort, not authoritative history replay
- delete/revoke remains explicitly non-gating

### 7.2 AI

Entry:

- parser output contract is frozen
- default field behavior is documented

Exit:

- OpenClaw wrapper parsing and fallback metadata are tested
- fixture corpus defines exact expected parser fields and non-exact fields
- any fallback from OpenClaw to heuristic is visible in logs/health/evidence

### 7.3 OKX Demo

Entry:

- demo-only guard confirmed
- supported vs unsupported configured actions documented

Exit:

- simulated path passes for all scoped actions
- configured demo REST path passes for supported actions in `ENV-DEMO-CRED`
- unsupported configured actions are explicitly excluded from pass criteria

### 7.4 Topic / Operator

Entry:

- topic target normalization rules frozen
- operator command list frozen
- expected operator-facing language is Chinese

Exit:

- outbound topic smoke passes in credentialed env
- inbound bot command path is legacy/internal only and not part of the intended supported operator scope
- docs do not treat inbound operator commands as a required readiness target
- topic logs and small Claw operator replies are verified in Chinese for the intended operator-facing flows

### 7.5 Web

Entry:

- six-digit PIN auth path defined
- required runtime actions enumerated
- browser-level validation plan respects server memory limits

Exit:

- login, `/api/state`, config mutation, channel lifecycle, inject, reconcile, topic-test, close, and reset-local-state have repo-local coverage
- any feature not present in current UI or endpoints is not claimed
- browser-level checks are executed with memory-safe discipline: single browser instance preferred, low tab count, low concurrency, and immediate browser cleanup after each check

## 8. Evidence Rules

Required evidence for future milestone execution:

- command line used
- config file used
- fixture ids or scenario ids used
- pass/fail result
- snapshot, artifact, or unittest output showing the asserted behavior

Preferred evidence locations:

- terminal output from `python3 -m unittest ...`
- JSON output from smoke scripts
- runtime artifacts under `runtime/.../direct-use.json`, `direct-use.txt`, `public-state.json`
- saved fixture result reports once the fixture harness is implemented

For the credentialed handoff, `scripts/m3_acceptance_prep.py` plus `docs/telegram-okx-openclaw-m3-acceptance-runbook.md` define the repo-side preflight capture and the exact manual `M3` sequence. They prepare evidence collection only; they do not satisfy `M3`.

## 9. Repo-Local Status At M2 Completion

The following repo-local checks were run while completing the offline/local integration milestone:

- `python3 -m unittest discover -s tests -v`
- `python3 scripts/run_demo_suite.py --config config.demo.local.json`
- `python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/messages`
- `python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/scenarios`
- `python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/html`

Observed results:

- unit suite is green: `123` tests ran, `0` failed
- `run_demo_suite.py` is green in repo-local offline mode
- `smoke_http_server.py` is intentionally non-gating in this environment when local socket bind is blocked by the sandbox
- `smoke_okx_demo.py` is intentionally non-gating in this environment when outbound network/DNS is unavailable; credentialed OKX demo coverage remains an `M3` gate
- all three fixture-suite commands are green against the deterministic corpus

Interpretation:

- the scoped repo-local `M2` command set is passable in offline/local mode
- remaining release gating is now about fixture acceptance packaging and `M3` credentialed-demo evidence, not repo-local drift

## 10. Immediate Next-Milestone Priorities

1. Preserve the current offline/local command set as the deterministic baseline for future repo-local changes.
2. Run `M3` only in an operator-owned demo environment with outbound network, OpenClaw access, and OKX demo credentials.
3. Keep `smoke_http_server.py` and `smoke_okx_demo.py` explicitly non-gating for offline/sandboxed execution where bind or network access is unavailable.
4. Re-evaluate the final acceptance gate only after the credentialed evidence package exists.
