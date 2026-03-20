# tg-okx-auto-trade

这是一个面向 **Telegram 公开频道信号** 的自动交易演示仓库。

它的主要职责是：

- 通过 `public_web` 抓取 Telegram 公开频道网页消息
- 对消息做归一化、版本化、去重和补偿式对账
- 调用 OpenClaw 提取交易意图
- 执行风险检查
- 把结果送到本地模拟执行器，或已配置的 **OKX Demo REST** 路径
- 通过 Web / CLI / topic 输出运行状态和操作结果

这份 README 只描述**当前仓库已经存在**的能力、命令和边界，不发明不存在的功能。

---

## 项目简介

这个仓库当前的定位很明确：

- 当前验收范围是 **`demo-only`**，也就是只验证模拟盘 / Demo 路径，不做实盘验收
- 当前主路径是 **`public_web-first`**，优先从 Telegram 公开网页抓取消息，而不是 MTProto / Telethon
- 当前只做 **`contracts only`**，也就是合约语义（swap / futures）
- 默认杠杆是 **`20x`**
- Web 默认端口是 **`6010`**
- `config.json` / `config.demo.local.json` 是**第一等控制面**；Web 和 CLI 会围绕它工作，并会把部分修改写回配置文件

如果你第一次接手这个仓库，可以把它理解成：

- 一套能在本地直接跑起来的 Telegram 信号处理与 Demo 交易验证链路
- 重点验证“采集 → 解析 → 风控 → Demo 执行 → Web/CLI/topic 可观测与控制”
- 当前不是实盘产品说明书，也不是“所有能力都已完成”的对外宣称

---

## 当前能力与边界

### 当前支持什么

当前仓库已经实现并对外可用的主要能力有：

- 通过 `public_web` 方式轮询 Telegram 公开频道网页
- 识别新消息、编辑消息，并做归一化、版本化、幂等处理和补偿式对账
- 调用 OpenClaw 提取交易意图；本地也保留启发式回退路径
- 执行风险检查，并进入执行流水线
- 支持本地模拟执行器
- 在配置好凭据时，支持走真实的 **OKX Demo REST** 请求路径
- 支持 topic 输出，支持 Web / CLI / small Claw 本地控制命令
- 提供 SQLite 持久化、Web 面板、CLI 命令、运行时状态文件，以及若干 smoke / verify 脚本
- 支持 `observe-only` 路径：记录预期交易，但不触碰 OKX 状态
- 配置文件支持写回与热加载

### 当前明确不支持或未完成的点

下面这些点要明确知道，不要从 README 里脑补成“已经支持”：

- 当前这轮验收是 **`demo-only`**，不做实盘测试，也不允许切到 live 路径
- 主支持路径是 **`public_web-first`**；`mtproto` / inbound bot path 即使有兼容残留，也不是当前主线能力
- 删除 / revoke 事件没有实现；`listen_deletes` 目前只是保留配置项
- OKX 私有 WebSocket 对账未启用；当前重点是本地状态与 Demo REST 路径
- 当前交易语义只覆盖合约，不覆盖现货等其他品类
- 手动 CLI / Web 注入默认走本地模拟执行器；只有显式选择时才会走已配置的 OKX Demo REST

### 关于 `demo-only` 的说明

这里要区分“当前验收范围”和“长期方向”：

- 当前这一轮 README、测试和操作说明，都按 **`demo-only`** 理解
- 仓库里已经存在 OKX Demo REST 真请求路径，用于带凭据的 Demo 验证
- 这不等于项目永远只能跑 Demo；只是你现在接手时，应该按 **Demo 验证仓库** 来操作

---

## 项目结构

当前仓库根目录主要结构如下：

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
scripts/
tests/
config.example.json
config.demo.local.json
.env.example
README.md
```

重点说明：

- `src/tg_okx_auto_trade/`：主业务代码
- `scripts/`：验证脚本、smoke 脚本、fixture 工具、demo 回归脚本
- `tests/`：单测与 fixture 相关测试
- `config.example.json`：通用示例配置
- `config.demo.local.json`：当前仓库用于本地 Demo 联调的配置文件
- `.env.example`：环境变量模板

---

## 快速开始

下面所有命令都默认在**仓库根目录**执行。

---

### 路径 A：从 `config.json` 开始

适合第一次本地起一个最小可运行配置。

#### 1）初始化配置并设置 Web PIN

```bash
python3 -m tg_okx_auto_trade.main init-config --config config.json --pin 123456
```

这会生成一个本地 `config.json`，并给 Web 登录设置一个 6 位 PIN。

#### 2）如果你不想把 PIN 哈希写进配置，也可以直接用环境变量

```bash
cp config.example.json config.json
export TG_OKX_WEB_PIN=123456
```

#### 3）先做只读检查

```bash
python3 -m tg_okx_auto_trade.main verify --config config.json
python3 -m tg_okx_auto_trade.main direct-use --config config.json
python3 -m tg_okx_auto_trade.main paths --config config.json
```

建议先看这三个命令：

- `verify`：当前配置能不能跑、缺什么
- `direct-use`：当前到底能直接怎么用
- `paths`：Web / runtime / topic / 配置路径都指向哪里

#### 4）启动服务

```bash
python3 -m tg_okx_auto_trade.main serve --config config.json
```

#### 5）打开 Web

```text
http://127.0.0.1:6010/login
```

#### 6）如果你只想先确认 HTTP 服务起来了

另开一个终端：

```bash
curl -i http://127.0.0.1:6010/login
curl -s http://127.0.0.1:6010/healthz
curl -s http://127.0.0.1:6010/readyz
```

---

### 路径 B：使用仓库内的 `config.demo.local.json`

适合直接沿用当前仓库已经整理好的 Demo 本地配置。

#### 1）先看路径和状态

```bash
python3 -m tg_okx_auto_trade.main paths --config config.demo.local.json
python3 -m tg_okx_auto_trade.main verify --config config.demo.local.json
python3 -m tg_okx_auto_trade.main direct-use --config config.demo.local.json
```

#### 2）如需把内联敏感信息迁移到本地 `.env`

```bash
python3 -m tg_okx_auto_trade.main externalize-secrets --config config.demo.local.json
```

#### 3）启动服务

```bash
python3 -m tg_okx_auto_trade.main serve --config config.demo.local.json
```

当前仓库里的这份 Demo 本地配置，通常会预接好这些内容：

- Web 登录页默认是 `http://127.0.0.1:6010/login`
- runtime 数据目录在 `runtime/demo-local/`
- operator topic 目标示例是 `-1003720752566:topic:2080`
- 已有 `public_web` 频道样例配置
- 交易模式保持 `demo`
- 默认杠杆 `20x`
- demo 本地配置里 AI agent 默认走 `tgokxai`

需要注意：

- 如果 `config.demo.local.json` 里启用了 `okx.enabled=true`，自动链路会走“已配置的 OKX Demo 路径”
- 但**手动注入消息**和 Web 里的 Demo 注入，默认仍然优先走**本地模拟执行器**
- 只有你显式指定时，才会走带凭据的 OKX Demo REST 路径

---

## Web 使用方式

Web 默认监听：

```text
http://127.0.0.1:6010/login
```

当前 Web 主要用于这些事情：

- 查看系统状态、健康状态、运行时摘要、`direct-use` 信息
- 查看消息、AI 决策、订单、仓位、日志、审计日志
- 修改 AI 配置、交易开关、风险参数、频道配置
- 配置 Telegram 采集与 operator topic
- 执行 `pause`、`resume`、`reconcile-now`、`topic smoke`、手动平仓等动作
- 从浏览器发起 Demo 信号注入，并显式选择模拟路径或已配置的 OKX Demo 路径

需要特别知道：

- Web 是控制面之一，但不是唯一控制面；配置文件仍然是第一等控制面
- 当服务已经启动后，修改 `web.host` / `web.port` 会写回配置，但不会让当前 HTTP 监听即时换绑；这种情况需要重启服务
- 本地快速检查时，可以直接访问 `/healthz` 和 `/readyz`，这两个端点不需要登录

---

## 常用 CLI 命令

下面列的是**当前主 CLI 已实际暴露**的命令。

### 1）启动与状态

```bash
python3 -m tg_okx_auto_trade.main serve --config config.json
python3 -m tg_okx_auto_trade.main verify --config config.json
python3 -m tg_okx_auto_trade.main paths --config config.json
python3 -m tg_okx_auto_trade.main direct-use --config config.json
python3 -m tg_okx_auto_trade.main snapshot --config config.json
```

### 2）注入测试信号

安全默认路径：**本地模拟执行**。

```bash
python3 -m tg_okx_auto_trade.main inject-message --config config.json --text "LONG BTCUSDT now"
```

如需显式走已配置的 OKX Demo REST 路径：

```bash
python3 -m tg_okx_auto_trade.main inject-message --config config.demo.local.json --real-okx-demo --text "LONG BTCUSDT now"
```

模拟编辑消息版本：

```bash
python3 -m tg_okx_auto_trade.main inject-message --config config.json --text "SHORT BTCUSDT now" --message-id 101 --event-type edit --version 2
```

### 3）运行时控制

```bash
python3 -m tg_okx_auto_trade.main pause --config config.json
python3 -m tg_okx_auto_trade.main resume --config config.json
python3 -m tg_okx_auto_trade.main reconcile --config config.json
python3 -m tg_okx_auto_trade.main topic-test --config config.json
python3 -m tg_okx_auto_trade.main close-positions --config config.json
python3 -m tg_okx_auto_trade.main reset-local-state --config config.json
```

### 4）配置辅助命令

```bash
python3 -m tg_okx_auto_trade.main init-config --config config.json --pin 123456
python3 -m tg_okx_auto_trade.main hash-pin --pin 123456
python3 -m tg_okx_auto_trade.main externalize-secrets --config config.demo.local.json
python3 -m tg_okx_auto_trade.main set-topic-target --config config.demo.local.json --target https://t.me/c/3720752566/2080
python3 -m tg_okx_auto_trade.main upsert-channel --config config.demo.local.json --name "VIP Public" --source-type public_web --channel-username https://t.me/s/lbeobhpreo
python3 -m tg_okx_auto_trade.main set-channel-enabled --config config.demo.local.json --channel-id vip_public --disabled
python3 -m tg_okx_auto_trade.main remove-channel --config config.demo.local.json --channel-id vip_public
```

### 5）operator 命令本地演练

```bash
python3 -m tg_okx_auto_trade.main operator-command --config config.json --text '/status'
```

### 6）查看当前 CLI 全量子命令

```bash
python3 -m tg_okx_auto_trade.main --help
```

当前 CLI 子命令包括：

- `serve`
- `verify`
- `paths`
- `direct-use`
- `snapshot`
- `inject-message`
- `pause`
- `resume`
- `reconcile`
- `topic-test`
- `operator-command`
- `set-topic-target`
- `upsert-channel`
- `set-channel-enabled`
- `remove-channel`
- `reset-local-state`
- `close-positions`
- `externalize-secrets`
- `init-config`
- `hash-pin`

---

## 配置说明

### `config` 是第一等控制面

当前仓库里，`config.json` / `config.demo.local.json` 不是附属文件，而是**第一等控制面**：

- Web 会读取它，也会把部分修改写回它
- runtime 会监控它的变化，并按配置重载
- `verify` / `paths` / `direct-use` 都以它为准输出当前实际状态

如果你接手这个仓库，建议先看配置文件，再看 Web；不要反过来理解。

---

### `public_web` 是当前主路径

如果要接 Telegram 公开频道，当前推荐并实际支持的方式，是给 `telegram.channels[]` 配一个 `source_type="public_web"` 的频道项，例如：

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

当前 `channel_username` 可接受：

- `@name`
- `https://t.me/name`
- `https://t.me/s/name`

`chat_id` 可接受：

- 原始 `-100...`
- `https://t.me/c/<chat>/<message>` 形式的链接

---

### operator topic

当前支持把 topic 作为运行输出和操作面的一部分。

关键字段在 `telegram` 下：

- `report_topic`
- `operator_target`
- `operator_thread_id`

说明：

- 如果同时设置了 `operator_target` 和 `report_topic`，以 `operator_target` 为准
- 目标既可以写成内部形式，例如 `-1001234567890:topic:123`
- 也可以写成 topic 链接，例如 `https://t.me/c/3720752566/2080`
- Web / CLI 会做归一化处理

---

### OKX Demo 凭据

当前范围里只验证 **OKX Demo**，不验证实盘。

关键字段在 `okx` 下：

- `enabled`
- `use_demo`
- `api_key`
- `api_secret`
- `passphrase`
- 以及对应的 `*_env` 字段

最小前提通常是：

- `okx.enabled=true`
- `okx.use_demo=true`
- 配好 Demo 环境的 `api_key` / `api_secret` / `passphrase`

也可以不把这些值直接写进配置，而是放进环境变量：

- `TG_OKX_OKX_API_KEY`
- `TG_OKX_OKX_API_SECRET`
- `TG_OKX_OKX_PASSPHRASE`

如果凭据环境不匹配，系统会把 OKX 的 `50101` 之类错误明确提示为环境不匹配问题。

---

### `.env` 的作用

当前 runtime 会优先加载“配置文件旁边的 `.env`”，找不到时再回退到仓库根目录 `.env`。已有 shell 环境变量优先级更高。

仓库里已有 `.env.example`，包含这些键：

```text
TG_OKX_WEB_PIN=123456
TG_OKX_TELEGRAM_BOT_TOKEN=
TG_OKX_OKX_API_KEY=
TG_OKX_OKX_API_SECRET=
TG_OKX_OKX_PASSPHRASE=
```

如果你已经把敏感信息写进 `config.demo.local.json`，可以用下面命令迁移：

```bash
python3 -m tg_okx_auto_trade.main externalize-secrets --config config.demo.local.json
```

---

### 关键默认值

当前仓库里几个重要默认值如下：

- 交易模式默认是 `demo`
- 执行模式默认是 `automatic`
- 默认杠杆是 `20`
- Web 默认端口是 `6010`
- `global_tp_sl_enabled` 默认关闭
- `position_mode` 在当前构建里应保持 `net`

---

## 当前限制 / 已知边界

- 当前验收和默认安全边界是 **`demo-only`**
- 当前主线采集路径是 **`public_web-first`**
- 当前只覆盖 **`contracts only`**
- 删除消息事件未实现，`listen_deletes` 仅保留配置位
- 私有 WebSocket 对账未启用；当前以本地状态与 Demo REST 为主
- 手动 CLI / Web 注入默认走本地模拟执行器；只有显式选择时才走配置好的 OKX Demo REST
- 当真实 OKX Demo REST 执行失败时，系统会自动 `pause`，避免继续发单
- `mtproto` 相关配置项可存在，但不属于当前主支持范围
- legacy inbound Telegram bot command 可能仍有内部残留，但不属于当前计划/支持主路径

---

## 测试与验证

### 单元测试

```bash
python3 -m unittest discover -s tests -v
```

### 基础验证

```bash
python3 scripts/verify_demo.py --config config.demo.local.json
```

### 常见 smoke

```bash
python3 scripts/smoke_web.py --config config.demo.local.json
python3 scripts/smoke_operator.py --config config.demo.local.json
python3 scripts/smoke_telegram.py --config config.demo.local.json
python3 scripts/smoke_config.py --config config.demo.local.json
python3 scripts/smoke_e2e.py --config config.demo.local.json
python3 scripts/smoke_http_server.py --config config.demo.local.json
python3 scripts/smoke_runtime.py --config config.demo.local.json
python3 scripts/smoke_okx_demo.py --config config.demo.local.json
python3 scripts/smoke_cli.py --config config.demo.local.json
```

### 完整本地 Demo 回归

```bash
python3 scripts/run_demo_suite.py --config config.demo.local.json
```

如果当前环境禁止外网，真实 topic smoke 或 OKX Demo smoke 可能会被跳过或失败；这不等于本地 runtime / Web / 模拟链路本身有问题。

---

## 里程碑更新过程

下面这段不是路线图许诺，而是对当前仓库这一轮 **M0–M5** 收口内容的中文总结。

### M0：测试文档基线

- 补齐测试文档、测试计划和最终验收文档
- 先把“怎么跑、怎么验、验收边界是什么”固定下来，形成可复查的文档基线

### M1：fixture / 测试资产基础

- 补齐本地 fixture、配置样例、运行时测试资产
- 让后续 smoke、回归、配置归一化验证有稳定输入

### M2 / M3：运行链与 Demo 执行能力收口

- 把消息处理链、AI 提取、风控、执行路径串起来
- 收口本地模拟执行与带凭据的 OKX Demo 执行能力
- 明确当前只做 Demo 验证，不把未完成能力混进验收口径

### M4：Web / Topic / 小 Claw 控制面、`public_web-first`、中文化、状态一致性

- 补齐 Web 控制面、topic 相关操作面和 small Claw 本地控制链路
- 明确 `public_web-first` 是当前主路径
- 强化 CLI / Web / runtime 文件之间的状态一致性和 `direct-use` 摘要
- 收口 Web operator-facing 中文化

### M5：恢复、异常、稳定性与最终验收

- 补恢复路径、异常提示、自动暂停、安全边界和稳定性验证
- 把最终验收口径收敛到可重复执行的 Demo 路径、验证脚本和已知边界说明上

---

## 最后提醒

如果你现在只是想**快速接手并跑起来**，建议按下面顺序做：

1. 先看 `config.demo.local.json`
2. 跑 `verify` / `direct-use` / `paths`
3. 再启动 `serve`
4. 打开 Web 看状态
5. 最后再做 `inject-message`、`topic-test`、`run_demo_suite.py` 这类验证

这样最不容易走偏。