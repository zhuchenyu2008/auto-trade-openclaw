# Testing Docs

这份仓库内测试文档集是在 `M0` 之后补齐的，用来把原先偏高层的外部计划，收敛成**和当前代码仓库直接对应**的测试与验收入口。

## 建议阅读顺序

1. `docs/telegram-okx-openclaw-milestones.md`
   - 看原始 operator 口径下的 `M0`–`M5` 里程碑、范围和当前状态
2. `docs/telegram-okx-openclaw-test-plan.md`
3. `docs/telegram-okx-openclaw-test-cases.md`
4. `docs/telegram-okx-openclaw-fixture-spec.md`
5. `docs/telegram-okx-openclaw-coverage-matrix.md`
6. `docs/telegram-okx-openclaw-final-test-plan.md`
7. `docs/telegram-okx-openclaw-m3-acceptance-runbook.md`
   - 看 operator 自己执行的带凭据 Demo 验收准备

## operator / 小 Claw 相关补充文档

如果你当前关心的是 operator 支持面，而不是单纯测试顺序，还应一起看：

- `docs/telegram-okx-openclaw-small-claw-system-prompt.md`
- `docs/telegram-okx-openclaw-small-claw-skill.md`

这两份文档负责补齐：

- 小 Claw 系统提示词草案
- 小 Claw 的 operator skill / 行为说明草案
- 项目内默认工作流、命令优先级和中文输出风格

## 当前仓库内可直接执行的验证资产

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

## 范围说明

这组文档服务的是当前收窄后的系统范围：

- `public_web` 公开 Telegram 频道采集
- 独立 OpenClaw AI 路径
- OKX demo only
- topic logging / small Claw operator surface
- Web control panel

仓库里仍可能保留更宽的表面，例如 `bot_api` 兼容项、已存储的 `mtproto` 配置等；除非文档里明确标成参考覆盖，否则它们都不应被视为当前 release-defining scope。
