# Telegram OKX OpenClaw Final Test Plan

Version: M0 milestone gate definition  
Date: 2026-03-19

## 1. Release Target

This final gate is for a directly usable demo-only release of the narrowed system:

- `public_web` public Telegram channel ingestion
- independent OpenClaw AI agent path
- OKX demo only
- topic logging and small Claw operator surface
- Web control panel

Passing this gate does not authorize live trading.

## 2. Milestone Gates

| Milestone | Goal | Required output | Gate status today |
| --- | --- | --- | --- |
| `M0` | repo-local docs baseline | the core testing docs under `docs/` are present and aligned to code | completed by this task |
| `M1` | deterministic fixture milestone | 120+ public-web fixtures, fixture runner, coverage manifest, updated case evidence | open |
| `M2` | repo-local integration milestone | scoped unit/smoke suite green in offline/local mode | open |
| `M3` | credentialed demo acceptance milestone | public_web + OpenClaw + topic + OKX demo pass in operator-owned env | open |

## 3. Final Acceptance Gate

The repo is only acceptable as a directly usable demo-only release when all items below are true.

### 3.1 Mandatory Green Gates

1. `M0`, `M1`, `M2`, and `M3` are all complete.
2. The fixture corpus contains at least `120` distinct samples and satisfies the bucket minimums in `docs/telegram-okx-openclaw-fixture-spec.md`.
3. Repo-local deterministic parser, dedup, reconcile, risk, web, topic-disabled, and recovery cases are green.
4. The current scoped smoke scripts are green or explicitly marked non-gating.
5. Credentialed demo validation proves:
   - one real `public_web` ingest path
   - one successful OpenClaw parse on the independent agent path
   - one successful outbound topic smoke
   - one successful OKX demo open
   - one successful OKX demo reverse or close
6. Demo-only guard remains locked and no live path is enabled anywhere in config validation, readiness output, or acceptance evidence.

### 3.2 Required Command Set At Gate Time

These commands must either pass or be replaced by an updated, equivalent repo-local command set documented in the same release branch:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_demo_suite.py --config config.demo.local.json
python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/messages
python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/scenarios
python3 scripts/run_fixture_suite.py --fixtures tests/fixtures/public_web/html
```

Notes:

- the three `run_fixture_suite.py` commands are planned and do not exist yet
- `run_demo_suite.py` is not a trustworthy final gate until the stale assertions documented in M0 are corrected

### 3.3 Required Evidence Package

The acceptance package must include:

- exact git revision tested
- exact config file used for offline and credentialed runs
- fixture manifest showing `120+` samples
- full unit output
- full smoke-suite JSON output
- per-fixture summary report
- screenshots or exported JSON from Web state only if they add evidence not already present in runtime artifacts
- credentialed demo evidence showing topic target used, public channel checked, and OKX demo order ids

## 4. Non-Negotiable Blockers

Any of the following blocks release:

- any live-trading path becomes enabled or even ambiguous
- public_web ingest is unverified or only documented, not executed
- OpenClaw path is only assumed and not evidenced in `ENV-DEMO-CRED`
- OKX demo execution is claimed for unsupported actions such as configured-path `update_protection`
- topic operator readiness is claimed without bot/topic wiring
- fixture milestone is skipped
- baseline test drift remains in release-gating commands

## 5. Current Status At M0

Current status is not release-ready.

Reasons:

- the 120+ fixture corpus does not exist yet
- the fixture runner does not exist yet
- `python3 -m unittest discover -s tests -v` was not fully green during M0 doc preparation
- `scripts/verify_demo.py` still contains a stale readiness assumption
- no credentialed demo validation was run in this task by design

## 6. Recommended Next Sequence

1. Complete `M1` by implementing the fixture corpus and fixture runner exactly against the fixture spec.
2. Repair current repo-local drift so `M2` has a clean deterministic baseline.
3. Run `M3` only in an operator-owned demo environment with outbound network, OpenClaw access, and OKX demo credentials.
4. Re-evaluate this final gate only after those artifacts exist.
