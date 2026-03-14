# auto-trade-openclaw

一个基于 **OpenClaw + OKX 官方 CLI/MCP** 的事件驱动自动交易项目：

- 监听 **Telegram 公共频道** 的新消息 / 编辑
- 把频道事件交给 **OpenClaw agent** 做结构化交易决策
- 用 **OKX 官方 `okx` CLI** 执行永续合约动作
- 把检测、决策、执行、整点报告发回指定 Telegram chat / topic

> 这不是“自己找机会”的自治量化系统。它是一个**频道事件驱动**的交易执行代理：
> **有频道新消息/编辑才决策；没有新频道事件时不生成新的交易意图。**

---

## 1. 功能概览

当前公开版已经包含：

- TG 公共频道 HTML 轮询监听
- 新消息 / 编辑消息检测
- OpenClaw agent 决策桥接
- OKX 官方 CLI 执行层
- 统一杠杆控制
- 仓位百分比区间控制（按可用 USDT）
- 交易日志回报到 Telegram topic
- 每小时整点账户报告
- systemd 常驻运行示例
- 单元测试 + smoke test

---

## 2. 设计边界

### 事件驱动，不主动找行情

系统只在以下事件发生时做交易判断：

- 频道出现 **新消息**
- 频道消息发生 **编辑**

不会做的事：

- 没有频道事件时自己盯盘找机会
- 自己根据 K 线波动临时改主观方向
- 自己脱离频道信号创造新交易想法

### 杠杆不由 agent 决定

杠杆由配置统一指定，例如：

- `20x`
- `10x`
- `15x`

交易决策层不负责决定杠杆。

### 仓位可受外部边界约束

当前实现支持按 **可用 USDT 百分比** 约束开仓 / 加仓：

- 最小比例 `min_position_pct`
- 最大比例 `max_position_pct`
- 默认比例 `default_position_pct`

例如：

- `0.4` → 最小 40%
- `0.8` → 最大 80%
- `0.6` → 默认 60%

若 agent 给出的仓位超出边界，执行层会自动夹到允许区间内。

---

## 3. 架构

```text
Telegram public channel
        ↓
PublicChannelWatcher
        ↓
Context Builder (recent messages + OKX snapshot)
        ↓
OpenClawAgentClient
        ↓
TradeDecision (strict JSON)
        ↓
OkxCliAdapter
        ↓
OKX official CLI
        ↓
OpenClawReporter → Telegram topic
```

模块说明：

- `tradeclaw/public_channel.py`
  - 抓取 `https://t.me/s/<channel>`
  - 检测新消息 / 编辑
- `tradeclaw/agent_client.py`
  - 调 `openclaw agent`
  - 给 agent 注入事件驱动边界与执行规则
- `tradeclaw/okx_cli.py`
  - 用官方 `okx` CLI 查询账户、持仓、下单、撤单、更新保护单
- `tradeclaw/reporter.py`
  - 用 `openclaw message send` 回报到 Telegram
- `scripts/hourly_report.py`
  - 生成整点账户报告

---

## 4. 目录结构

```text
tradeclaw/
  __init__.py
  agent_client.py
  app.py
  cli.py
  config.py
  models.py
  okx_cli.py
  public_channel.py
  reporter.py
  utils.py

scripts/
  bootstrap_okx_config.py
  hourly_report.py
  install_okx_skills.py
  smoke_test.sh
  tradeclaw.service.example

tests/
fixtures/
config.example.json
state/.gitkeep
```

---

## 5. 配套 skill

仓库里额外带了一个 companion skill：

- `skills/tg-okx-tradeclaw-ops/`

它适合给另一个 OpenClaw 实例使用，用来：

- 解释这套 TradeClaw 是怎么工作的
- 检查为什么下单 / 没下单
- 改杠杆、仓位范围、source channel、topic 目标、报告格式
- 重置本地运行态并从“现在”重新建基线
- 修改 topic 行为与 systemd / OpenClaw 配置

也就是说：
- **这个仓库**负责放项目代码
- **这个 skill**负责放“怎么运维/改参/排障”

## 6. 依赖

### 必要依赖

- Python 3.10+
- Node.js 18+
- OpenClaw
- OKX 官方 CLI / MCP

安装官方 OKX 组件：

```bash
npm install -g @okx_ai/okx-trade-cli @okx_ai/okx-trade-mcp
```

> 官方 OKX skills 不是必需项；这个仓库已经把安装脚本保留下来，便于你在自己的 OpenClaw workspace 中补装。

---

## 6. 配置

复制示例配置：

```bash
cp config.example.json config.json
```

### `source.channels`

要监听的 Telegram **公共频道用户名**：

```json
["some_public_channel"]
```

### `okx.profile`

- `demo`
- `live`

### `okx.margin_mode`

- `cross`
- `isolated`

### `okx.position_mode`

- `long_short_mode`
- `net_mode`

### `okx.default_leverage`

统一杠杆，所有开仓 / 加仓按这个值走。

### `okx.min_position_pct / max_position_pct / default_position_pct`

按可用 USDT 百分比限制开仓 / 加仓。

### `report.target / report.thread_id`

交易日志发到哪里：

- 私聊：`target = "123456789"`
- Telegram 论坛 topic：
  - `target = "-1001234567890"`
  - `thread_id = "42"`

### `runtime.execution_enabled / runtime.dry_run`

推荐上线顺序：

1. `execution_enabled=false, dry_run=true` → 先影子跑
2. 观察 topic 里的理解与执行计划
3. 再切成 `execution_enabled=true, dry_run=false`

---

## 7. OKX 账户配置

如果还没有 `~/.okx/config.toml`，先生成模板：

```bash
python3 scripts/bootstrap_okx_config.py
```

然后填入：

- `api_key`
- `secret_key`
- `passphrase`
- `site`

建议权限：

- Read
- Trade
- 不要开 Withdrawal
- 最好绑服务器 IP 白名单

模板大致长这样：

```toml
default_profile = "demo"

[profiles.live]
site = "global"
api_key = "REPLACE_ME"
secret_key = "REPLACE_ME"
passphrase = "REPLACE_ME"

[profiles.demo]
site = "global"
api_key = "REPLACE_ME"
secret_key = "REPLACE_ME"
passphrase = "REPLACE_ME"
demo = true
```

---

## 8. 运行方式

### 单次跑一轮

```bash
PYTHONPATH=$PWD python3 -m tradeclaw.cli --config config.json --once
```

### 常驻运行

```bash
PYTHONPATH=$PWD python3 -m tradeclaw.cli --config config.json
```

### 查看解析后的配置

```bash
PYTHONPATH=$PWD python3 -m tradeclaw.cli --config config.json --dump-config
```

---

## 9. systemd 部署

仓库里带了示例：

- `scripts/tradeclaw.service.example`

你需要把里面的路径改成你自己机器上的实际路径。

一个典型流程：

```bash
sudo cp scripts/tradeclaw.service.example /etc/systemd/system/tradeclaw.service
sudo systemctl daemon-reload
sudo systemctl enable --now tradeclaw.service
sudo systemctl status tradeclaw.service
```

---

## 10. 整点报告

整点报告脚本：

- `scripts/hourly_report.py`

当前报告偏向 **交易账户视角**，优先输出：

- 合约浮盈亏
- 合约已实现盈亏（追踪以来）
- 今日合约盈亏
- 现货资产波动（今日 / 追踪以来）
- 账户总权益
- USDT 余额 / 可用余额
- 当前合约仓位

如果你要定时发送，可用 OpenClaw cron 把它的 stdout 投递到指定 Telegram topic。

---

## 11. 测试

### 单元测试

```bash
PYTHONPATH=$PWD python3 -m unittest discover -s tests -v
```

### smoke test

```bash
sh scripts/smoke_test.sh
```

smoke test 会做：

1. 单元测试
2. `okx market ticker BTC-USDT`
3. `okx-trade-mcp --help`
4. 用 fixture 跑一轮 TradeClaw

---

## 12. 脱敏说明

这个公开仓库已经去掉了以下内容：

- 真实 OKX key / secret / passphrase
- 真实 Telegram 频道 / 群组 / topic 配置
- 实际运行态 `state/*.json`
- 审计日志 / 下单痕迹
- 机器私有路径与私人账号目标

你需要自己补：

- `config.json`
- `~/.okx/config.toml`
- systemd 服务中的真实路径
- 你自己的 Telegram 路由目标

---

## 13. 已知限制

### 只支持公共频道 HTML 轮询

这意味着：

- 只支持公共频道
- 编辑检测是 best-effort
- 延迟是秒级轮询，不是 Telegram MTProto push

如果你要监听私有频道，建议下一步把 `source` 适配成 Telethon / MTProto。

### 首版仍然偏“可用原型”

这套东西已经能跑，但你仍应把它视为：

- **可交付试运行版**
- 不是无脑全自动印钞机
- 上线前先 demo / 小仓位 / 强观察

---

## 14. 常见问题

### Q: 它会不会没新消息时自己找机会交易？
不会。当前架构是严格事件驱动。

### Q: 杠杆是谁决定的？
由配置统一指定，不由 agent 临时决定。

### Q: 仓位是谁决定的？
可以由 agent 在配置允许范围内决定，执行层会做边界收敛。

### Q: 现在用什么换仓？
默认是 **可用 USDT** 作为保证金基准来计算仓位和张数。

---

## 15. 后续可扩展方向

- Telethon 私有频道监听
- topic 内直接对话控制（改杠杆 / 改仓位范围 / pause / resume）
- 更完整的多 TP 分批处理
- 更强的持仓管理语义理解
- Web dashboard / metrics

---

## 16. License

暂未指定。若你准备公开长期维护，建议补一个明确许可证。
