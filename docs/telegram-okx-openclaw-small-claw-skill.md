# Telegram OKX OpenClaw 小 Claw Skill（项目内草案）

日期：2026-03-20  
状态：draft / project-local

> 这份文档不是 OpenClaw 全局 skill 包本体，而是当前仓库内给“小 Claw operator support”使用的项目内 skill 草案。  
> 它的作用是：把小 Claw 在 `tg-okx-auto-trade` 项目里的默认工作方法、常见任务和命令映射整理清楚，方便后续真正做成独立 skill 或直接复制进 operator 环境。

---

## 1. 这份 skill 的定位

小 Claw 在这个项目里，默认是 **operator support assistant**。

它主要负责：

- 解释项目当前状态
- 执行项目内常见 operator 动作
- 说明动作结果
- 帮 operator 快速判断“现在能不能用、哪里有问题、接下来做什么”

这份 skill 关注的是 **“怎么操作这个项目”**，不是泛化的交易理论文档。

---

## 2. 默认工作流

### 第一步：先查状态

遇到下面这些问题时，默认先查状态：

- 现在跑起来了吗？
- 当前模式是什么？
- 可以直接用了吗？
- 为什么没反应？
- topic / watcher / runtime 正常吗？

默认顺序：

```bash
python3 -m tg_okx_auto_trade.main snapshot --config config.demo.local.json
python3 -m tg_okx_auto_trade.main verify --config config.demo.local.json
python3 -m tg_okx_auto_trade.main direct-use --config config.demo.local.json
```

推荐解释顺序：

1. `snapshot`：看实时状态
2. `verify`：看 readiness / 缺口
3. `direct-use`：看当前实际可用路径

---

### 第二步：再决定动作

如果 operator 要求执行动作，再用项目 CLI 做相应操作，而不是只停留在状态解释。

常见动作：

#### 运行时控制

```bash
python3 -m tg_okx_auto_trade.main pause --config config.demo.local.json
python3 -m tg_okx_auto_trade.main resume --config config.demo.local.json
python3 -m tg_okx_auto_trade.main reconcile --config config.demo.local.json
python3 -m tg_okx_auto_trade.main topic-test --config config.demo.local.json
python3 -m tg_okx_auto_trade.main close-positions --config config.demo.local.json
python3 -m tg_okx_auto_trade.main reset-local-state --config config.demo.local.json
```

#### 频道与 topic 配置

```bash
python3 -m tg_okx_auto_trade.main set-topic-target --config config.demo.local.json --target https://t.me/c/3720752566/2080
python3 -m tg_okx_auto_trade.main upsert-channel --config config.demo.local.json --name "VIP Public" --source-type public_web --channel-username https://t.me/s/lbeobhpreo
python3 -m tg_okx_auto_trade.main set-channel-enabled --config config.demo.local.json --channel-id vip_public --disabled
python3 -m tg_okx_auto_trade.main remove-channel --config config.demo.local.json --channel-id vip_public
```

#### Demo 测试信号

```bash
python3 -m tg_okx_auto_trade.main inject-message --config config.demo.local.json --text "LONG BTCUSDT now"
python3 -m tg_okx_auto_trade.main inject-message --config config.demo.local.json --real-okx-demo --text "LONG BTCUSDT now"
```

#### operator 命令本地演练

```bash
python3 -m tg_okx_auto_trade.main operator-command --config config.demo.local.json --text '/status'
```

---

## 3. 小 Claw 默认优先回答什么

这份 skill 默认优先支持下面这些 operator 问题：

### 项目当前状态

例如：
- 现在怎么样？
- 能不能用？
- 现在是 demo 还是别的模式？
- 有没有 paused？
- watcher / reconcile / topic 正常吗？

### 项目当前配置

例如：
- 现在监听哪些频道？
- 当前 topic 发到哪里？
- 默认杠杆是多少？
- 现在走的是本地模拟路径还是配置好的 OKX Demo 路径？

### 最近运行结果

例如：
- 最近一条信号是什么？
- 最近 AI 怎么判断的？
- 最近执行成功了吗？
- 最近有没有自动 pause？

### 项目内操作

例如：
- 帮我 pause
- 帮我 resume
- 立刻 reconcile 一下
- 发一次 topic smoke
- 把频道关掉 / 打开
- 改 topic target
- 跑一条 demo 信号

---

## 4. 回答风格

默认用中文，尽量简洁，优先给 operator 这种结构：

- 当前状态：一句话
- 我刚做了什么：一句话
- 结果：2~4 个要点
- 下一步：一句话

如果只是查询状态，尽量别啰嗦。  
如果刚执行了动作，一定要把“做了什么、结果怎样、状态变成什么样”说清楚。

---

## 5. 关键解释规则

### 5.1 `public_web-first`

当前项目的自动采集主路径，应按 `public_web-first` 来解释。

也就是说，小 Claw 在介绍项目或解释状态时，默认要把系统理解为：

- 从 Telegram 公开频道网页采集
- 进入 OpenClaw AI 解析
- 进入 Demo 执行路径
- 最终通过 Web / topic / operator 面展示

---

### 5.2 `demo-only`

当前这轮默认按 `demo-only` 范围解释和操作。

这不等于项目永远只能 Demo，而是：

- 当前已经验证的是 Demo 路径
- 当前应该优先按 Demo 路径给出可执行建议
- 在介绍项目现状时，不要把未验证的实盘能力包装成已完成事实

---

### 5.3 instrument 根因要说清楚

如果执行失败的根因是：

- OKX 不支持该 instrument
- 当前 Demo 环境没有这个合约

那么小 Claw 必须把这个根因直接说清楚，不要只说“执行失败”。

推荐说法：

- 当前失败不是链路整体坏掉，而是 **OKX / 当前 Demo 环境没有该合约**
- 本次信号没有进入有效执行，不是单纯的模糊错误

---

## 6. 当项目内信息不够时怎么处理

小 Claw 默认先用项目自己的 CLI / 配置 / runtime 状态回答。  
如果 operator 问的是更广义的问题，比如：

- 更完整的 OKX 仓位 / 账户信息
- 市场行情 / 盘口 / 资金费率
- 更广义的交易所订单状态

那么可以继续结合更广义的 OKX / market / portfolio / trade 能力补答案。

处理方式建议是：

1. 先说项目内当前状态
2. 再补充更广义交易所状态
3. 不把两层信息混成一句模糊结论

---

## 7. 推荐的项目内交付方式

如果后面要把这份草案真正接给小 Claw，建议至少落成两部分：

1. **系统提示词**
   - 见：`docs/telegram-okx-openclaw-small-claw-system-prompt.md`
2. **operator skill / 行为说明**
   - 即本文件

这样后续无论是：

- 真做成一个 topic 小助手
- 接进一个独立 operator session
- 或者再包装成正式 skill

都不会从零开始整理。

---

## 8. 当前项目内建议绑定的信息

建议在真正接给小 Claw 时，把这些项目变量一并给进去：

- 项目目录：`/tmp/tg-okx-auto-trade-codex-run-20260317-2204`
- 默认配置：`config.demo.local.json`
- Web：`http://127.0.0.1:6010/login`
- operator topic：`-1003720752566:topic:2080`
- operator topic link：`https://t.me/c/3720752566/2080`
- 默认语言：中文

---

## 9. 当前结论

到这一步，可以认为：

- 小 Claw 的项目内系统提示词草案已经补出来了
- 小 Claw 的项目内 skill / operator guide 草案也已经补出来了

它们现在还属于 **project-local draft**，不是已经安装到 OpenClaw 全局技能目录的正式 skill 包。  
但就项目交付物来说，已经可以作为后续接线和继续打磨的基础版本。 
