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

## 项目状态

### ✅ Phase 0 — 基础设施 + 数据接入（已完成）

- 项目骨架（uv + pyproject.toml + ruff + mypy strict + pytest）
- 配置管理（pydantic-settings + .env）
- 结构化日志（structlog）
- DuckDB schema + repository
- HTTP/SOCKS 代理支持（应对区域封锁）
- **Binance Futures REST 客户端**（async + 指数退避重试）
- **5 源 OI 加权资金费率**（Binance + OKX + Bybit + Bitget + Gate.io）
- **OI 历史回填**（Binance openInterestHist，5m/15m/.../1d，最多 30 天）
- 批量 upsert 写入器（Polars → DuckDB Arrow 桥接）

### ✅ Phase 1 — 分析引擎（已完成）

| 切片 | 模块 | 内容 |
|---|---|---|
| ✅ 1 | `analysis/resample` + `analysis/structure` | 1m → 任意 TF 重采样；分形 swing 识别；BOS / CHoCH 事件检测 |
| ✅ 2 | `analysis/volume` + `analysis/profile` | Per-bar Delta / 累计 CVD / VWAP + σ 通道 / Volume Profile (POC / VAH / VAL) |
| ✅ 3 | `analysis/zones` | Order Block 识别（依赖结构事件）+ Fair Value Gap 识别（纯几何） + mitigation 跟踪 |

### ✅ Phase 2 — 流动性引擎（已完成）

| 切片 | 模块 | 内容 |
|---|---|---|
| ✅ 1 | `analysis/liquidity` | Equal Highs / Equal Lows 流动性池识别（贪心 1-D 聚类 + sweep 跟踪） |
| ✅ 2 | `analysis/stop_hunt` | Stop Hunt / 流动性扫荡检测（fakeout vs clean break，三维置信度） |
| ✅ 3 | `analysis/divergence` | 多指标背离（CVD / Volume / OI），indicator-agnostic 统一抽象 |

### ✅ Phase 3 — 上下文聚合 + 告警（已完成）

| 切片 | 模块 | 内容 |
|---|---|---|
| ✅ 1 | `analysis/wyckoff` | Wyckoff 阶段状态机（11 状态 + 12 事件类型 + 纯函数 FSM） |
| ✅ 2 | `analysis/wyckoff` | 完善事件检测器（AR/ST/SOS/LPS + 1H 闸控 + range 重锚） |
| ✅ 3 | `analysis/context` | 情境聚合报告（7 子上下文 + Scorecard + render_text/markdown） |
| ✅ 4 | `notifications/` | 告警推送（Telegram / 企微 / 飞书，三 channel 并发分发） |

### ✅ Phase 4 — AI 分析 + 定时推送（已完成）

| 切片 | 模块 | 内容 |
|---|---|---|
| ✅ 1 | `analysis/llm` | LLM 分析模块（OpenAI 兼容 API，结构化 prompt，中文/英文输出） |
| ✅ 2 | `scheduler` | 定时调度器（APScheduler，每天 8:05 日K / 每小时 1H / 每 4 小时 4H） |
| ✅ 3 | `scheduler` | 自动数据拉取（分析前自动回填 K 线 + 更新 OI + 更新资金费率） |

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

### 调度时间表

| 时间 | 任务 | 时间周期 | 高周期参考 |
|------|------|----------|------------|
| 每天 08:05 (北京时间) | 日 K 分析 | 1D | - |
| 每小时 | 1H 分析 | 1H | 4H |
| 每 4 小时 | 4H 分析 | 4H | 1D |

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

### AI 分析 + 推送

| 命令 | 用途 |
|---|---|
| `pa ai-analyze --timeframe 1h` | AI 分析并推送到飞书（自动拉取数据） |
| `pa ai-analyze --timeframe 1h --dry-run` | 试运行，只打印不推送 |
| `pa ai-analyze --timeframe 1h --no-fetch` | 跳过数据拉取，使用已有数据 |
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
