# Price Action Trading Assistant

一个面向比特币合约交易者的**价格行为辅助决策系统**。

不抓新闻、不堆指标、不自动下单。
只做一件事：**把市场结构、流动性、量价关系，翻译成人能读懂的「市场情境报告」。**

核心交付方式：**定时调度 → 数据采集 → AI 分析 → 飞书推送**

---

## 核心理念

- **价格是唯一的真相** — 一切信息最终都反映在 K 线上
- **市场由流动性驱动** — 合约本质是猎杀止损的游戏
- **辅助决策,不替代决策** — 扣扳机的永远是人
- **可解释性优先** — 每一个标注都能追溯到 K 线和逻辑

## 交易理论基石

Wyckoff · Smart Money Concepts · ICT · VSA · 量价背离 · 流动性猎杀

## 文档

- [系统架构与设计理念](./docs/ARCHITECTURE.md)

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     Scheduler (APScheduler)                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                  │
│  │ 每天 8:05│    │ 每小时   │    │ 每 4 小时│                  │
│  │ 日K 分析 │    │ 1H 分析  │    │ 4H 分析  │                  │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘                  │
│       │               │               │                         │
│       ▼               ▼               ▼                         │
│  ┌──────────────────────────────────────────┐                   │
│  │         Data Fetcher (自动拉取)          │                   │
│  │  K线回填 + OI快照 + 资金费率             │                   │
│  └────────────────────┬─────────────────────┘                   │
│                       │                                         │
│                       ▼                                         │
│  ┌──────────────────────────────────────────┐                   │
│  │         Analysis Engine (分析引擎)       │                   │
│  │  结构/量价/流动性/Wyckoff → 结构化数据    │                   │
│  └────────────────────┬─────────────────────┘                   │
│                       │                                         │
│                       ▼                                         │
│  ┌──────────────────────────────────────────┐                   │
│  │         LLM Analysis (AI 解读)           │                   │
│  │  结构化数据 + Prompt → OpenAI API → 报告  │                   │
│  └────────────────────┬─────────────────────┘                   │
│                       │                                         │
│                       ▼                                         │
│  ┌──────────────────────────────────────────┐                   │
│  │         Notification (推送)              │                   │
│  │  飞书 / Telegram / 企业微信              │                   │
│  └──────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 快速开始

```bash
# 1. 安装依赖
uv sync --extra dev

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，配置：
#   - HTTP_PROXY_URL（如需要）
#   - LLM_API_KEY（OpenAI 或兼容接口）
#   - LLM_BASE_URL（默认 https://api.openai.com/v1）
#   - LLM_MODEL（默认 gpt-4o）
#   - LARK_WEBHOOK_URL（飞书机器人 webhook）

# 3. 验证交易所连通性
uv run pa check-proxy

# 4. 初始化 DuckDB
uv run pa init-db

# 5. 回填初始数据
uv run pa backfill --days 7          # 7 天 1m K 线
uv run pa backfill-oi --days 7       # 7 天 OI 历史
uv run pa poll-funding               # 资金费率快照

# 6. 测试 AI 分析（手动触发一次）
uv run pa ai-analyze --timeframe 1h --dry-run    # 试运行，不推送
uv run pa ai-analyze --timeframe 1h              # 实际推送

# 7. 启动定时调度器
uv run pa schedule-start
```

---

## AI 分析流程

每次调度触发时自动执行：

```
1. 拉取最新数据
   ├── 回填最近 1 天 K 线 (Binance REST)
   ├── 更新 OI 快照
   └── 更新资金费率 (5 源加权)

2. 运行分析引擎
   ├── 结构事件 (BOS/CHoCH)
   ├── 订单块 + FVG
   ├── 流动性池 + Stop Hunt
   ├── 量价背离 (CVD/Volume/OI)
   └── Wyckoff 阶段状态机

3. 调用 LLM
   ├── 结构化数据 → Prompt
   ├── OpenAI 兼容 API
   └── 返回中文分析报告

4. 推送到飞书
   └── Markdown 格式报告
```

---

## AI 核心分析与研判法则

本系统区别于普通的指标罗列式 AI 分析，它将**资深自营交易员的交易逻辑与风险控制底线**以确定性规则的形式注入到了 AI 提示词（Prompt）中。AI 报告严格遵循以下三大核心分析框架进行逻辑推理：

### 1. SMC 市场结构与高周期趋势判定

趋势的识别不依赖于任何有滞后性的均线（MA/EMA），而是基于**聪明钱/结构化交易（Smart Money Concepts, SMC）**中经典的高低点断裂判定法：
* **威廉分形点探测 (Swing High/Low)**：使用 $N=2$ 的滑动窗口（Williams 5-Bar Fractal）无未来函数地标记出局部摆动高点与低点。
* **收盘实体突破判定 (BOS / CHoCH)**：只有当 K 线的**收盘价 (Close)** 实体突破前高或跌破前低时，才会触发**结构断裂 (BOS)** 或 **性格改变 (CHoCH)**。影线（插针）仅视为流动性猎杀 (Stop Hunt)，从而极大规避了交易所插针诱多/诱空的噪音。
* **多周期对齐 (HTF Alignment)**：AI 会对比工作周期与高周期（如 4H vs 1D）的趋势一致性，给出双周期的方向性共振评估。

### 2. CVD / OI / 价格共振黄金法则

为了科学甄别“真假突破”，AI 会根据订单流与量价动力数据，严格对照下表进行推演：

| 价格走势 | 持仓量 (OI) | CVD (成交量偏差) | 资金费率 (Funding) | 市场研判结论 | 交易应对建议 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **上涨** | 增加 | 增加 | - | **强力主动多头建仓**（真突破，看涨信号强） | 顺势做多 |
| **上涨** | 减少 | 平缓 / 减少 | - | **空头爆仓/挤压驱动的反弹**（弱反弹，防假突破） | 严防高位追多 |
| **下跌** | 增加 | 减少 | - | **强力主动空头建仓**（真跌破，看空信号强） | 顺势做空 |
| **下跌** | 减少 | 平缓 / 增加 | - | **多头踩踏/爆仓割肉平仓**（超跌，防假跌破） | 寻找底部反弹 |
| **震荡** | 显著增加 | - | - | **主力密集对倒/建仓蓄势**（波动即将来临） | 保持观望，等待突破 |
| **上涨** | - | - | **平稳 / 走低** | **现货主动买盘驱动**（极度健康，高确定性） | 坚定看涨做多 |
| **下跌** | - | - | **持续上升** | **散户加杠杆抄底背离**（多头陷阱，极危险） | 容易连环爆仓，禁止做多 |

### 3. 宏观周期与资金燃料定律 (4H / 1D 专属)

当分析 4H 或 1D 等高级别周期时，系统会自动激活**【宏观周期与资金燃料定律】**，引导 AI 将杠杆拥挤度作为趋势燃料进行宏观研判：
* **牛市蓄力/空头燃料 (法则 A)**：若高周期趋势看涨，而当前 4H/1D 资金费率转为**负费率**或处于极低位，代表散户因回调而恐慌性做空或进行对冲。在宏观牛市中，这些空头持仓将成为未来向上爆发的**“空头燃料 (Short Squeeze 燃料)”**。AI 此时会坚定寻找底部 Spring 逢低做多，忽略常规的负费率看空逻辑。
* **熊市诱多/多头燃料 (法则 B)**：若高周期趋势看跌，而当前 4H/1D 资金费率保持**高正费率**，代表散户正在加杠杆死扛或盲目抄底。在宏观熊市中，这些拥挤的多头将沦为价格进一步闪崩崩盘的**“多头燃料 (Long Liquidation 燃料)”**。AI 会坚决寻找阻力位逢高做空，严禁任何抄底推荐。

---

### 调度时间表与对齐机制

为了使策略报告分析与技术指标判定永远建立在**已完全闭合、不漂移的蜡烛图**之上，且避开整点交易所的网络请求洪峰，系统所有分析均在**K线收盘后延迟 5 分钟**高精度对齐触发：

| 定时触发点 (北京时间) | 触发频率 | 策略分析周期 | 高周期参考 | 定时器机制 | 说明 |
|:---|:---|:---|:---|:---|:---|
| **每天 08:05** | 每天一次 | **1D（日K）** | 无 | `CronTrigger` | 宏观大周期研判，激活【宏观周期与资金燃料定律】（基于 LLM 分析与推送）。自动切除未收盘的今日线。 |
| **每小时的第 5 分钟** (如 13:05, 14:05...) | 每小时一次 | **1H（小时K）** | 无 | `CronTrigger` | 日内短线价格驱动力研判，使用**轻量化规则驱动力推导算法（非 AI 接口，极速且省成本）**推送精简版报告。自动切除未收盘的本小时线。 |
| **每 4 小时收盘后 5 分钟** (00/04/08/12/16/20:05) | 每4小时一次 | **4H（波段K）** | **1D** | `CronTrigger` | 中线波段策略分析，激活【宏观周期与资金燃料定律】（基于 LLM 分析与推送），自动切除未收盘的本4小时线。 |

### 并发安全互斥锁与防抖去重机制 (`asyncio.Lock` + `Cache`)

在重合时间点（例如 **08:05** 或 **20:05**，1H/4H/1D 定时任务会同时被并发唤醒），系统运行高度安全的**串行互斥锁与时间防抖去重算法**：
*   **并发写库互斥防撞**：声明全局异步锁 `asyncio.Lock`。三路任务并发激活时，第一个抢占到锁的任务执行网络拉取并将最新分钟线、持仓与加权费率写入 DuckDB；其余任务在锁外挂起等待。这彻底根治了多任务并发读写 DuckDB 产生的数据库连接死锁异常。
*   **30秒网络拉取防抖**：第一个任务写完放锁后，后续挂起任务依次被唤醒。它们会进行 30 秒时间防抖校验——若上一次成功拉取时间发生于 30 秒内，系统会 **100% 自动跳过重复的网络拉取与写入**，直接复用既有数据库开始分析。这实现了 **0 次重复网络开销**，完全消除了触发交易所 429 限频的危险。

### 分周期飞书机器人路由推送

为了便于交易团队对不同频次的报告进行精细化协作管理，系统支持将 1H、4H、1D 报告路由发送到不同的飞书群/机器人中（配置参见下文的 `.env` 变量）：
*   **1H 短线报告** $\rightarrow$ `LARK_WEBHOOK_URL_1H`（路由至短线高频交流群）
*   **4H 波段报告** $\rightarrow$ `LARK_WEBHOOK_URL_4H`（路由至中长线策略群）
*   **1D 宏观报告** $\rightarrow$ `LARK_WEBHOOK_URL_1D`（路由至核心战略决策群）
*   *注：若未配置特定周期变量，系统将自动平滑降级，回退使用全局通用通道（如全局 `LARK_WEBHOOK_URL`、Telegram 或企业微信）进行统一推送。*

### LLM 配置（.env）

```bash
# OpenAI
LLM_API_KEY=sk-your-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o

# DeepSeek（推荐，便宜且中文好）
LLM_API_KEY=sk-your-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 其他 OpenAI 兼容接口
LLM_API_KEY=your-key
LLM_BASE_URL=https://your-api.com/v1
LLM_MODEL=your-model
```

### 通知渠道配置（.env）

至少配置一个：

```bash
# 飞书群机器人（推荐）
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/YOUR-KEY
LARK_SIGNING_SECRET=your_signing_secret  # 可选

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# 企业微信群机器人
WECHAT_WORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR-KEY
```

---

## CLI 命令一览

### 数据管理

| 命令 | 用途 |
|---|---|
| `pa init-db` | 初始化 DuckDB schema |
| `pa show-config` | 打印当前生效配置（密钥自动脱敏） |
| `pa check-proxy` | 并行 ping 交易所诊断网络 |
| `pa backfill --days N` | 回填 N 天历史 1m K 线 |
| `pa backfill-oi --days N --period 5m` | 回填 N 天 OI 历史 |
| `pa poll-oi` | 一次性 OI 快照 |
| `pa poll-funding` | 一次性多所加权资金费率 |

### 分析命令

| 命令 | 用途 |
|---|---|
| `pa analyze-structure --timeframe TF` | Swing + BOS/CHoCH 事件报告 |
| `pa analyze-volume --timeframe TF` | Delta/CVD + VWAP + Volume Profile |
| `pa analyze-zones --timeframe TF` | Order Block + FVG 当前生效区列表 |
| `pa analyze-liquidity --timeframe TF` | Equal Highs/Lows 流动性池 |
| `pa analyze-stop-hunts --timeframe TF` | Stop Hunt 检测 |
| `pa analyze-divergences --timeframe TF` | CVD/Volume/OI 背离 |
| `pa wyckoff --timeframe TF` | Wyckoff 阶段状态机 |
| `pa context-report --timeframe TF [--htf TF]` | 情境聚合报告 |

### 策略分析与报告推送

| 命令 | 用途 |
|---|---|
| `pa ai-analyze --timeframe 1h` | AI 分析并推送到飞书（自动拉取数据） |
| `pa ai-analyze --timeframe 1h --dry-run` | 试运行，只打印不推送 |
| `pa ai-analyze --timeframe 1h --no-fetch` | 跳过数据拉取，使用已有数据 |
| `pa driver-report --timeframe 1h` | 规则驱动力分析报告并推送（自动拉取数据，不消耗 AI 额度） |
| `pa driver-report --timeframe 1h --dry-run` | 价格驱动力报告试运行预览 |
| `pa driver-report --timeframe 1h --no-fetch` | 跳过拉取直接根据缓存运行价格驱动力报告 |
| `pa schedule-start` | 启动定时调度器（后台运行） |
| `pa send-alert --timeframe TF [--htf TF]` | 推送规则引擎报告（非 AI） |

---

## 目录结构

```
pa_assistant/
├── config.py                # pydantic-settings
├── logging.py               # structlog 封装
├── cli.py                   # typer 命令行
├── scheduler.py             # 定时调度器 + 自动数据拉取
├── ingestion/               # 数据接入层（无分析逻辑）
│   ├── _http.py             # 共享 async HTTP 基类（重试 + 代理）
│   ├── binance.py           # Binance Futures REST + OI 历史迭代器
│   ├── okx.py               # OKX V5
│   ├── bybit.py             # Bybit V5
│   ├── bitget.py            # Bitget V2 Mix
│   ├── gateio.py            # Gate.io Futures V4
│   └── funding.py           # FundingProvider 抽象 + 5 源自聚合
├── analysis/                # 纯函数分析层（无 IO）
│   ├── resample.py          # 1m → 任意 TF (Polars group_by_dynamic)
│   ├── structure.py         # 分形 swing + BOS/CHoCH 状态机
│   ├── volume.py            # Delta / CVD / VWAP + σ 通道
│   ├── profile.py           # Volume Profile (POC / VAH / VAL)
│   ├── zones.py             # Order Block + FVG + mitigation 跟踪
│   ├── liquidity.py         # Equal Highs/Lows 流动性池
│   ├── stop_hunt.py         # Stop Hunt / 流动性扫荡检测
│   ├── divergence.py        # 多指标背离（CVD/Volume/OI）
│   ├── wyckoff.py           # Wyckoff 阶段状态机（FSM）
│   ├── driver.py            # 价格驱动力规则推导模块（多空发力与建仓识别）
│   ├── context.py           # 情境聚合报告（7 子上下文 + Scorecard）
│   └── llm.py               # LLM 分析模块（OpenAI 兼容 API）
├── notifications/           # 推送通道
│   ├── telegram.py          # Telegram Bot API
│   ├── wechat.py            # 企业微信群机器人
│   └── lark.py              # 飞书群自定义机器人
└── storage/                 # 持久层
    ├── schema.py            # DuckDB DDL
    ├── repository.py        # Database 连接管理
    └── writers.py           # 批量 upsert（幂等）

tests/                       # pytest 单测（304 个）
docs/                        # 设计文档
```

---

## 设计原则

1. **`ingestion/` 与 `analysis/` 完全解耦** — 分析层不知道数据从哪来，只接受 Polars DataFrame
2. **持久只存 1m K 线 + 5m OI**，更高 TF 通过 `resample_ohlcv()` 按需派生
3. **抽象优先于实现** — `FundingProvider` Protocol 让 Coinglass / 自聚合 / 未来其他源零代码切换
4. **失败可降级** — 单交易所故障不影响整体（asyncio.gather + 部分成功语义）
5. **mypy strict + ruff + pytest** — 304 个测试，类型完全覆盖
6. **纯函数分析** — 所有 analysis 模块无 IO、无副作用，frozen-slots dataclass 输出
7. **LLM 解耦** — 分析引擎输出结构化数据，LLM 只负责解读，可随时切换模型/提供商

---

## 网络环境注意事项

⚠️ **某些 VPS IP 会被交易所 CloudFront/CDN 封锁**：

| 交易所 | 状态 | 备注 |
|---|---|---|
| Binance | 大量 IP 段返回 HTTP 451 | 封锁在 CDN 层 |
| Bybit | 美国/部分 IP 段返回 HTTP 403 | 同上 |
| OKX / Bitget / Gate.io | 大部分 IP 通 | 一般无障碍 |

**解决方案**：配置 `HTTP_PROXY_URL=http://127.0.0.1:7890`（clash/v2ray/wireguard）。

任何一两个交易所失败时，资金费率聚合器会**自动跳过失败源、用剩下的源算加权值**，不会整体失败。
