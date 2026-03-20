# Telegram OKX OpenClaw Milestones

Version: original operator milestone plan (M0–M5)
Date: 2026-03-20

## 1. Purpose

This document restores the **original milestone contract** confirmed with the operator in chat.

It exists to avoid mixing two numbering systems:

- the **original execution plan** used in chat: `M0` → `M5`
- the later **repo-local gate shorthand** used in some docs: `M0` → `M3`

When reporting progress to the operator, prefer this file's `M0`–`M5` numbering.

## 2. Scope

The milestone plan is for the narrowed system only:

- `public_web` public Telegram channel ingestion
- independent AI agent path (`tgokxai` / OpenClaw)
- OKX **demo only**
- topic logging / operator surface
- Web control panel
- small Claw operator support

Out of scope unless explicitly reopened:

- live trading
- MTProto/private channels
- letting Codex perform external integration testing directly

## 3. Operator Rules Bound To These Milestones

### 3.1 Validation ownership

- Codex may write code, tests, fixtures, and docs.
- Final validation is owned by the main session.
- Large UI milestones may use Agent Browser.
- External validation (Telegram / OKX / browser-linked operator actions) should be executed by the main session, not delegated to Codex.

### 3.2 Reporting rule

Every 5-minute progress report should keep the **existing concise report format**, and additionally state:

- current milestone (`M0`–`M5`)
- milestone progress / sub-progress
- remaining milestones

### 3.3 Language rule

Default language for operator-facing control surfaces is **Chinese**:

- topic logs should be Chinese by default
- small Claw operator replies should be Chinese by default
- milestone progress reports to the operator should be Chinese
- when validating M4 topic / small Claw operator workflows, Chinese output is part of the expected behavior, not an optional polish item

### 3.4 Agent Browser memory rule

When Agent Browser is used for `M4` Web validation, server memory safety is a hard constraint:

- prefer a single browser instance
- keep tab count low
- avoid parallel browser-heavy runs by default
- keep sessions short and close pages/browser processes immediately after each check
- prefer lighter read/state checks before escalating to heavier browser flows
- do not trade off server stability for faster UI coverage

## 4. Milestones

## M0 — 测试文档全面收口

### Goal
Turn the test docs into an executable test system before broad implementation proceeds.

### Required outputs
1. Master test plan
2. Detailed test cases
3. Simulated channel fixture specification
4. Final acceptance checklist

### Exit criteria
- requirements map to concrete tests
- actions map to fixtures + assertions + validation items
- docs stop being purely descriptive

### Current status
**Completed**

### Evidence / delivered assets
- `docs/telegram-okx-openclaw-test-plan.md`
- `docs/telegram-okx-openclaw-test-cases.md`
- `docs/telegram-okx-openclaw-fixture-spec.md`
- `docs/telegram-okx-openclaw-coverage-matrix.md`
- `docs/telegram-okx-openclaw-final-test-plan.md`

### Milestone commit
- `ea85fab docs: add repo-local testing baseline for demo-only scope`

---

## M1 — 建立 100+ 条模拟频道信息测试资产

### Goal
Build at least 100 simulated channel messages (planned at 120+) with realistic coverage.

### Required outputs
- fixture corpus
- generator/runner support
- parser/action expectations
- edit/replay/dedup/reconcile scenarios

### Exit criteria
- every fixture has expected outcome
- parser/action/schema checks pass
- edit/dedup/reconcile scenarios are exercised

### Current status
**Completed**

### Evidence / delivered assets
- deterministic public-web fixture corpus (`123` total samples in the delivered corpus)
- `scripts/run_fixture_suite.py`
- `scripts/generate_public_web_fixtures.py`
- `tests/fixtures/public_web/...`

### Milestone commit
- `f2d25ee test: add deterministic public_web fixture corpus`

---

## M2 — 解析层 + 动作矩阵 + 幂等 / 编辑 / 补抓 全面打透

### Goal
Make the read → parse → action path stable before broader acceptance.

### Focus
- independent AI path hits reliably
- action mapping stays consistent
- `ignore` does not trigger accidental execution
- edits do not double-trade
- dedup/idempotency work
- reconcile does not create duplicates
- ambiguous symbols are not over-guessed

### Exit criteria
- action matrix green
- fixture suite green
- edit / dedup / reconcile cases green
- unit suite green

### Current status
**Completed**

### Evidence / delivered assets
- repo-local parser/action/risk/reconcile path stabilized
- unit suite green
- fixture suite green

### Milestone commit
- `afd289b test: align repo-local demo smoke gates`

---

## M3 — OKX Demo 执行层详测

### Goal
Move from “can parse” to “can execute demo orders stably”.

### Focus
- auth
- account/config
- leverage setup
- market / limit
- open / add / reduce / close / reverse / cancel
- status writeback
- position/order state sync
- failure logging
- automatic pause behavior
- hard demo-only enforcement

### Exit criteria
- OKX demo mainline repeatedly usable
- failures are explicit, not silent
- demo-only guard remains hard-locked

### Current status
**Completed**

### Evidence / delivered assets
- real demo REST execution path verified for open / reverse
- close signal tail fixed later in final cleanup
- demo-only guard still locked

### Milestone commit
- `047e03f 修复：close 信号先同步真实 demo 持仓再平仓`

---

## M4 — Web / Topic / 小 Claw 控制面测试

### Goal
Move from “can run” to “can use”.

### Required validation

#### Web
Use browser-level validation for the important operator surface:
- login page
- PIN auth
- dashboard
- mode display
- leverage display
- TP/SL controls and ratios
- enabled channels
- logs page
- positions / PnL display
- config save
- channel management
- run demo signal
- Web state consistent with CLI/runtime

#### Topic / small Claw
Using the configured topic target:
- signal notifications
- AI success/failure reporting
- order success/failure reporting
- position change reporting
- small Claw status queries
- small Claw mode / positions / channels / recent signal queries
- small Claw operator support via skill/docs

### Exit criteria
- Web core usable
- topic reporting usable
- small Claw usable for day-to-day operator support
- config / runtime / displayed state match

### Current status
**Partially complete / not closed**

### What is already done
- repo-local Web smoke and HTTP smoke exist
- outbound topic smoke has succeeded
- operator/runtime/config smoke paths exist
- topic and operator docs/runbooks exist

### What is still missing for milestone closure
- full browser-level Agent Browser pass across the operator Web UI
- full topic / small Claw end-to-end operator workflow validation in Chinese
- explicit proof that Web / runtime / topic displays stay consistent across the main control surfaces

---

## M5 — 恢复、异常、稳定性与最终验收

### Goal
Move from “can run” to “directly usable”.

### Required validation
- restart/recovery behavior
- repeated message handling after restarts
- short network interruption recovery
- stability / soak behavior
- final acceptance package with explicit evidence
- final direct-use decision

### Exit criteria
- critical recovery cases validated
- stability acceptable for intended demo-only usage
- final acceptance package assembled
- operator can reasonably treat the narrowed system as directly usable

### Current status
**Partially complete / not closed**

### What is already done
- many recovery and dedup paths are covered in tests and fixtures
- final acceptance evidence has been partially assembled
- credentialed and fallback validation evidence exists

### What is still missing for milestone closure
- explicit restart/network-recovery validation runbook execution
- longer stability / soak style confirmation
- final acceptance package assembled against the original M5 checklist, not only the repo-local M0–M3 gate shorthand

---

## 5. Current Overall Status

### Completed milestones
- `M0`
- `M1`
- `M2`
- `M3`

### Remaining milestones
- `M4`
- `M5`

## 6. Mapping To Repo-Local Gate Docs

Some repo docs use the later shorthand gate system:

- repo-local `M0` ≈ original docs baseline
- repo-local `M1` ≈ original fixture milestone
- repo-local `M2` ≈ original parser/integration stabilization
- repo-local `M3` ≈ original credentialed acceptance slice

Important:

- this shorthand is **not** a replacement for the original `M4` / `M5` operator plan
- when operator-facing progress is reported, use **this original M0–M5 plan** unless the operator explicitly asks for the shorthand gate view

## 7. Recommended Next Sequence

1. Finish **M4** explicitly, using browser-level Web validation plus topic / small Claw operator-flow validation in Chinese.
2. Then finish **M5** explicitly, focusing on recovery, abnormal cases, stability, and the final acceptance package.
3. Keep 5-minute progress reports tied to this milestone file until `M5` is closed.
