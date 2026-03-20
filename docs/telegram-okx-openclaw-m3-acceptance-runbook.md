# Telegram OKX OpenClaw M3 Acceptance Runbook

Version: M3A repo-side prep  
Date: 2026-03-20

## 1. Purpose

This runbook prepares the repo for `M3` credentialed demo acceptance.

It does not validate `M3` by itself.

`M3` is only complete when the main session runs the credentialed demo flow in an operator-owned environment and captures the required evidence.

## 2. What This Runbook Covers

- exact repo-local preflight commands to save before the credentialed run
- exact manual operator steps to execute next
- evidence fields to verify in runtime artifacts
- claim boundaries so repo-side prep is not overstated

## 3. Preconditions

Before the main session starts `M3`, confirm all of the following:

- outbound network is allowed in the operator-owned environment
- OpenClaw CLI is reachable for `ai.provider="openclaw"`
- OKX demo credentials are available through env or local `.env`
- if inbound operator-topic commands are part of the acceptance run, the Telegram bot token is available and the bot can receive topic messages
- if outbound topic smoke is part of the acceptance run, the configured operator topic target is reachable from the local OpenClaw send path
- at least one enabled `public_web` source channel is configured and points at a real public Telegram channel page

## 4. Repo-Local Preflight

Generate the prep summary first:

```bash
python3 scripts/m3_acceptance_prep.py --config config.demo.local.json --format markdown > runtime/demo-local/m3-acceptance-prep.md
```

That helper is repo-side only. It does not contact Telegram or OKX. It reports:

- current wiring and activation summary
- redacted secret presence status
- enabled `public_web` channels
- exact runtime artifact paths
- the manual `M3` command sequence
- evidence checks and claim boundaries

Then save the deterministic repo-local baseline:

```bash
python3 scripts/run_demo_suite.py --config config.demo.local.json
python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/messages
python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/scenarios
python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/html
```

If you want a clean local runtime for easier evidence review, archive the existing runtime artifacts first and then run:

```bash
python3 -m tg_okx_auto_trade.main reset-local-state --config config.demo.local.json
```

Save the preflight state immediately before the credentialed run:

```bash
python3 -m tg_okx_auto_trade.main verify --config config.demo.local.json > runtime/demo-local/m3-verify-before.json
python3 -m tg_okx_auto_trade.main direct-use --config config.demo.local.json --json > runtime/demo-local/m3-direct-use-before.json
```

## 5. Manual M3 Execution Steps

The main session should execute these steps next.

### 5.1 Start The Runtime

```bash
python3 -m tg_okx_auto_trade.main serve --config config.demo.local.json
```

Keep that service running while the credentialed acceptance is performed.

### 5.2 Prove Outbound Topic Smoke

From another shell, run:

```bash
python3 -m tg_okx_auto_trade.main topic-test --config config.demo.local.json > runtime/demo-local/m3-topic-test.json
```

Evidence to keep:

- CLI output from the command above
- the actual Telegram topic message or screenshot proving delivery into the configured topic

### 5.3 Prove Real `public_web` Ingest

Wait for one real post from one of the configured public Telegram channels to ingest through the running service.

After the service has processed that post, save:

```bash
python3 -m tg_okx_auto_trade.main snapshot --config config.demo.local.json > runtime/demo-local/m3-snapshot-after-public-web.json
```

Evidence checks in that snapshot:

- the newest message should show `payload.adapter` = `public_web`
- the newest AI decision should show `payload.raw.parser_source` = `openclaw`
- do not count a snapshot whose newest AI decision shows `heuristic_fallback` as the OpenClaw pass

Do not use `inject-message` as the primary evidence for this step.

### 5.4 Prove OKX Demo Open And Reverse Or Close

Use the credentialed runtime path only.

The preferred path is:

1. let the real `public_web` signal produce a supported configured OKX demo action such as `open_long`, `open_short`, `reverse_to_long`, `reverse_to_short`, or `close_all`
2. if the real signal opened a position, close it through the configured path with:

```bash
python3 -m tg_okx_auto_trade.main close-positions --config config.demo.local.json --symbol <symbol-from-open> > runtime/demo-local/m3-close-command.json
```

After the open and after the reverse or close, capture:

```bash
python3 -m tg_okx_auto_trade.main snapshot --config config.demo.local.json > runtime/demo-local/m3-snapshot-after-close.json
```

Evidence checks:

- the newest order should have a non-empty `exchange_order_id`
- the saved evidence package should include the OKX demo order ids for the open and the reverse or close
- do not claim configured-path support for `update_protection`, trailing protection, or ratio-based global TP/SL

Optional supplemental OKX-only evidence:

```bash
python3 scripts/smoke_okx_demo.py --config config.demo.local.json > runtime/demo-local/m3-smoke-okx-demo.json
```

This is useful as extra OKX demo evidence, but it is not a substitute for real `public_web` ingest evidence.

## 6. Required Evidence Package

Save all of the following:

- `runtime/demo-local/m3-acceptance-prep.md`
- terminal output from `run_demo_suite.py`
- terminal output from all three `run_fixture_suite.py` commands
- `runtime/demo-local/m3-verify-before.json`
- `runtime/demo-local/m3-direct-use-before.json`
- `runtime/demo-local/m3-topic-test.json`
- `runtime/demo-local/m3-snapshot-after-public-web.json`
- `runtime/demo-local/m3-close-command.json`
- `runtime/demo-local/m3-snapshot-after-close.json`
- Telegram-side evidence for the outbound topic smoke
- OKX demo-side evidence showing the order ids used in the credentialed run

## 7. Claim Boundaries

Keep the final acceptance wording honest:

- repo-side prep and documentation improvements are not `M3` completion
- `M3` requires real credentialed execution in the main session
- `public_web` ingest is not proven by simulated injects
- OpenClaw success is not proven when the saved AI decision shows `heuristic_fallback`
- topic readiness is not topic delivery
- OKX demo readiness is not OKX demo execution
