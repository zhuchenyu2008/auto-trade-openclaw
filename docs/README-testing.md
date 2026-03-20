# Testing Docs

This repository did not have a repo-local testing doc set before M0. These files replace the earlier high-level external plan with repo-local guidance tied to the current codebase.

Read them in this order:

1. `docs/telegram-okx-openclaw-milestones.md` for the original operator-approved `M0`–`M5` milestone contract and current status
2. `docs/telegram-okx-openclaw-test-plan.md`
3. `docs/telegram-okx-openclaw-test-cases.md`
4. `docs/telegram-okx-openclaw-fixture-spec.md`
5. `docs/telegram-okx-openclaw-coverage-matrix.md`
6. `docs/telegram-okx-openclaw-final-test-plan.md`
7. `docs/telegram-okx-openclaw-m3-acceptance-runbook.md` for the operator-owned credentialed demo handoff

Current repo-local execution assets:

- `tests/test_app.py`
- `scripts/run_demo_suite.py`
- `scripts/verify_demo.py`
- `scripts/smoke_cli.py`
- `scripts/smoke_runtime.py`
- `scripts/smoke_web.py`
- `scripts/smoke_operator.py`
- `scripts/smoke_telegram.py`
- `scripts/smoke_http_server.py`
- `scripts/smoke_okx_demo.py`
- `scripts/m3_acceptance_prep.py`

Scope note:

- These docs gate the narrowed M0 system: `public_web` public Telegram ingestion, independent OpenClaw AI path, OKX demo only, topic logging / small Claw operator surface, and the Web control panel.
- The repo still contains broader surfaces such as `bot_api` helpers and stored `mtproto` config, but those are not the release-defining scope for this doc set unless called out as reference-only coverage.
