# Price Action Trading Assistant — 系统架构与设计理念

> 一个面向比特币合约交易者的价格行为辅助决策系统。
> 不预测新闻，不依赖指标堆砌，只服务于「读懂市场结构 + 识别主力行为」。

---

## 一、设计理念（Philosophy）

### 1.1 核心信条

1. **价格是唯一的真相（Price is the only truth）**
   一切基本面、消息面最终都会反映到 K 线上。系统不抓取新闻、不做情绪分析，只研究价格本身留下的痕迹。

2. **市场由流动性驱动（Liquidity drives the market）**
   合约市场的本质是猎杀流动性。止损、爆仓、挂单簿就是燃料。系统的核心任务是**找到流动性聚集区**，并识别主力是在**收集（accumulate）还是分发（distribute）**。

3. **量价是因，K 线是果**
   单看 K 线会被骗，单看成交量没有方向。系统始终把 **价格结构 + 成交量 + 持仓量 + 资金费率** 放在同一个上下文里观察。

4. **辅助决策，不替代决策（Assist, not automate）**
   系统不自动下单、不给出"买/卖"信号。它的输出是**结构化的市场观察报告**：现在处于什么阶段、流动性在哪里、风险/机会比是什么。**扣扳机的永远是人。**

5. **可解释性优先（Explainability first）**
   每一个提示、每一个标注，都必须能追溯到一段 K 线和一段逻辑。**黑盒模型一律拒绝**，机器学习只用于辅助识别形态，不用于直接产生信号。

6. **聚焦胜过铺张（Focus over breadth）**
   只做 **BTC 永续合约**。一个标的做深做透，远胜于全市场扫描的"广而浅"。
   一个数据源能解决问题，绝不引入第二个。

### 1.2 不做什么（Anti-goals）

- ❌ 不做新闻情绪分析、社交媒体爬虫
- ❌ 不堆砌技术指标（MACD、RSI、布林带等仅作辅助参考，不作为信号源）
- ❌ 不提供"圣杯策略"、不承诺胜率
- ❌ 不内嵌自动交易（第一阶段），避免使用者把它当成提款机
- ❌ 不做 Alt 币全市场扫描
- ❌ 不做多交易所聚合（除非真的需要交叉验证才考虑）

---

## 二、核心交易理论基石

系统的所有模块围绕以下几套互相补充的理论构建：

| 理论 | 关注点 | 在系统中的体现 |
|---|---|---|
| **Wyckoff 方法** | 主力的吸筹/派发周期 | 阶段识别（PS/SC/AR/ST/Spring/UTAD…） |
| **Smart Money Concepts (SMC)** | 订单块、公允价值缺口、流动性扫荡 | Order Block、FVG、Liquidity Sweep 标注 |
| **ICT 概念** | 流动性池、Killzone、市场结构突破 | BOS/CHoCH 检测、Killzone 时段提醒 |
| **量价分析（VSA）** | 努力 vs 结果、量价背离 | Volume Climax、No Demand Bar、Effort vs Result |
| **猎杀止损 / 流动性猎杀** | 主力在结构高低点的扫单行为 | Stop Hunt 检测 + 反转概率评估 |
| **持仓量 / 资金费率** | 多空力量与杠杆情绪 | OI 突变、加权费率极值、爆仓热力图 |

---

## 三、数据源与范围（Scope）

### 3.1 标的

- **BTC/USDT 永续合约**（Binance Futures）
- 周期：1m / 5m / 15m / 1h / 4h / 1d

### 3.2 数据源分工

| 数据 | 来源 | 接入方式 | 用途 |
|---|---|---|---|
| K 线 OHLCV | **Binance Futures** | WebSocket（实时）+ REST（历史） | 价格结构基础 |
| 逐笔成交（Trades） | **Binance Futures** | WebSocket | CVD、Delta、主动买卖 |
| 持仓量（OI） | **Binance Futures** | REST 轮询（1 分钟） | 加仓/减仓判断 |
| 爆仓流（Liquidations） | **Binance Futures** | WebSocket（forceOrder） | 流动性猎杀确认 |
| 多空账户比 | **Binance Futures** | REST 轮询（5 分钟） | 散户立场参考 |
| **持仓量加权资金费率** | **Coinglass API** | REST 轮询（5-15 分钟） | 多空杠杆情绪极值 ⭐ |
| 单所资金费率（备份） | Binance | REST | Coinglass 故障时降级使用 |

> **为什么资金费率用 Coinglass 而不是 Binance？**
> Binance 的资金费率只反映 Binance 一家。Coinglass 的 OI 加权资金费率聚合了主流交易所，按持仓量加权，能更真实地反映**全市场杠杆情绪**。在极值（如 +0.05% 以上或 -0.02% 以下）时，反向指标价值显著高于单所数据。

### 3.3 关于 Coinglass 的注意事项

1. **付费 API**：所需的加权资金费率属于 Coinglass 付费 API，最低套餐起步（具体价格以官方为准）。
2. **频率限制**：付费层一般 30-120 req/min，缓存 + 5-15 秒轮询足够覆盖需求。
3. **降级方案**：API 不可用时，系统自动切换到"自聚合模式"——从 Binance / OKX / Bybit 各自拉资金费率 + OI，按 OI 加权计算。
4. **抽象隔离**：Coinglass 调用封装在独立的 `funding_provider` 模块，未来可无缝替换数据源。

---

## 四、系统架构（High-Level Architecture）

由于范围聚焦 BTC 单标的、单交易所，**整个系统跑在单个 Python 进程内**即可，不需要消息总线、不需要微服务。

```
┌──────────────────────────────────────────────────────────────────┐
│                        单 Python 进程                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                     用户交互层 (UI)                          │ │
│  │   Web Dashboard (FastAPI + React)  │  Telegram Bot          │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ▲                                   │
│  ┌───────────────────────────┴────────────────────────────────┐ │
│  │              应用服务层 (Application Services)               │ │
│  │  实时监控  │  回放/复盘  │  告警引擎  │  交易日志             │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ▲                                   │
│  ┌───────────────────────────┴────────────────────────────────┐ │
│  │              分析引擎 (Analysis Engine) ⭐ 核心               │ │
│  │  ┌────────┬────────────┬────────┬────────┬──────────────┐ │ │
│  │  │ 结构   │ 流动性引擎  │  VSA   │ Wyckoff│ 上下文聚合器  │ │ │
│  │  │ Module │ Liquidity   │ Module │ FSM    │ Aggregator    │ │ │
│  │  └────────┴────────────┴────────┴────────┴──────────────┘ │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ▲                                   │
│  ┌───────────────────────────┴────────────────────────────────┐ │
│  │              数据处理 (Polars + NumPy)                      │ │
│  │  多周期对齐 │ 增量计算 │ CVD/Delta/VWAP/Volume Profile      │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ▲                                   │
│  ┌───────────────────────────┴────────────────────────────────┐ │
│  │                  存储层 (DuckDB 单文件)                      │ │
│  │       K 线 │ Trades │ OI │ Funding │ 爆仓 │ 情境快照         │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ▲                                   │
│  ┌───────────────────────────┴────────────────────────────────┐ │
│  │                    数据接入 (Ingestion)                      │ │
│  │  ┌─────────────────────┐    ┌─────────────────────────┐   │ │
│  │  │ Binance Connector   │    │ Coinglass Connector     │   │ │
│  │  │ (WS + REST)         │    │ (REST + 自聚合降级)     │   │ │
│  │  │ K线/Trades/OI/爆仓  │    │ 加权资金费率            │   │ │
│  │  └─────────────────────┘    └─────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

**进程内通信**：模块之间用 `asyncio.Queue` 或简单的回调，不上 Redis/NATS。
**横向扩展**：当且仅当未来需要时才考虑拆分（YAGNI 原则）。

---

## 五、模块详细设计

### 5.1 数据接入层（Ingestion）

**职责**：稳定、低延迟地获取数据，封装两个 Connector。

#### Binance Connector
- 基于 `python-binance` 或 `ccxt.pro` 的异步 WebSocket
- 订阅流：`btcusdt@kline_*`、`btcusdt@aggTrade`、`btcusdt@forceOrder`
- REST 轮询：OI（1 分钟）、多空比（5 分钟）、历史 K 线补齐
- 断线重连 + 序列号校验（防止漏单导致 CVD 失真）

#### Coinglass Connector
- 基于 `httpx` 的异步 REST 客户端
- 5-15 秒轮询加权资金费率（具体频率根据套餐限频调整）
- 内置 LRU 缓存避免重复请求
- 异常时自动降级到"Binance + OKX + Bybit 自聚合"模式
- API Key 通过环境变量注入，绝不入库

### 5.2 存储层（Storage）

**DuckDB 单文件**，分表存储：

| 表名 | 内容 | 主键 |
|---|---|---|
| `kline_1m` | 1 分钟 K 线（其他周期实时聚合） | `open_time` |
| `trades` | 逐笔成交（按天分区） | `trade_id` |
| `oi_1m` | 持仓量快照 | `timestamp` |
| `funding_weighted` | Coinglass 加权资金费率 | `timestamp` |
| `liquidations` | 爆仓事件 | `timestamp + side` |
| `context_snapshots` | 情境报告历史（用于复盘） | `timestamp` |
| `journal` | 交易日志 | `trade_id` |

DuckDB 优势：零运维、单文件、SQL 直接查、Polars 原生互通。
未来数据量上 GB 级再考虑 ClickHouse。

### 5.3 数据处理层（Processing）

- **统一使用 Polars**（不用 pandas，性能差 5-10 倍且 API 更现代）
- **多周期对齐**：1m 是基础，其他周期用 `group_by_dynamic` 实时聚合
- **增量计算**：所有指标支持滑动窗口流式更新
- **派生数据**：
  - **CVD**（Cumulative Volume Delta）
  - **Delta per bar**（每根 K 线主动买卖差）
  - **Volume Profile**（POC / VAH / VAL）
  - **VWAP**（带 1σ/2σ 通道）
  - **OI Delta**（持仓量变化，区分增仓/减仓）

### 5.4 分析引擎（Analysis Engine）— 系统的大脑

#### 5.4.1 市场结构模块（Structure）

- Swing High / Swing Low 检测（基于分形或 ZigZag）
- **BOS（Break of Structure）**：趋势延续确认
- **CHoCH（Change of Character）**：趋势反转早期信号
- HH/HL vs LH/LL 序列追踪
- Range Detection：横盘区间识别（Wyckoff 阶段判断的前提）

#### 5.4.2 流动性引擎（Liquidity Engine）⭐ 核心

合约交易的本质就是流动性博弈，这是整个系统最关键的部分。

- **流动性池识别（Liquidity Pools）**
  - Equal Highs / Equal Lows（等高/等低，散户止损密集区）
  - 前期 Swing High / Low（经典止损位）
  - 整数关口（心理价位）

- **Stop Hunt / Liquidity Sweep 检测**
  - 模式：快速插针突破 → 成交量放大 → 价格迅速收回
  - 配合 Trades 数据确认：扫单时是否有大额逆向主动单接住
  - 输出：**猎杀概率评分** + 反转目标位

- **Order Block（订单块）**
  - BOS/CHoCH 之前的最后一根反向 K 线
  - 标注未被触及的 OB 作为潜在反应区

- **FVG（Fair Value Gap，公允价值缺口）**
  - 三根 K 线形成的失衡区
  - 区分 Bullish / Bearish FVG，标注未填补的缺口

- **爆仓热力图**
  - 基于 Binance 爆仓流 + OI 估算多空爆仓密集区
  - 与价格结构叠加，标注"磁吸位"

#### 5.4.3 量价分析模块（VSA）

核心思想：**努力 vs 结果**（Effort vs Result）

- **量价背离检测**
  - 价格新高，CVD/Delta 不创新高 → 看跌背离
  - 价格新低，CVD/Delta 不创新低 → 看涨背离
  - 多周期背离叠加（1h 背离 + 15m 结构破坏 = 高质量信号）

- **Volume Climax**：异常放量收窄 K 线（吸筹/派发标志）
- **No Demand / No Supply Bar**：低量小阳/小阴（趋势衰竭）
- **Effort vs Result 异常**：大量但价格不动 → 主力吸收

#### 5.4.4 Wyckoff 阶段状态机（FSM）

- Accumulation / Distribution Schematic（PS、SC、AR、ST、Spring、Test、SOS、LPS…）
- 状态机驱动，结合 Range + Volume + 流动性扫荡综合判断
- 状态转移条件可配置，便于回测调参

#### 5.4.5 上下文聚合器（Context Aggregator）

把上面所有模块的输出**聚合成一份"市场情境报告"**：

```yaml
现在 BTC 正处于：
  趋势:        4h 上涨 / 15m 回调
  结构:        15m 形成 CHoCH，可能反转
  位置:        正在测试 1h Bullish Order Block
  流动性:      下方 Equal Lows 已被扫荡（Stop Hunt 概率 78%）
  量价:        15m 出现看涨背离（CVD 未创新低）
  持仓:        OI 在扫荡时下降 → 空头减仓
  加权费率:    -0.012%（Coinglass，偏空，可能反向）
  关键位:      上方 FVG 67,200-67,450 未填补
  情境评分:    长仓机会 7.5 / 10
```

**这是系统的最终交付物。不是信号，是情境。**

### 5.5 应用服务层

- **实时监控（Live Watch）**：图表实时叠加所有标注（OB、FVG、Liquidity、Wyckoff 阶段）
- **回放/复盘（Replay）**：按任意时间点回放，逐 K 线推进，验证系统判断与实际走势
- **告警引擎（Alerts）**：基于"情境组合"触发（例如：CHoCH + 流动性扫荡 + 量价背离 同时成立）
- **交易日志（Journal）**：手动记录交易，系统自动关联当时的市场情境快照，用于事后复盘

### 5.6 用户交互层

- **Web Dashboard**（主入口）：FastAPI 后端 + React 前端 + TradingView Lightweight Charts
- **Telegram Bot**：把高质量情境报告推送到手机
- **CLI 工具**：批量回测、数据导出、运维（基于 `typer`）

---

## 六、数据流示例（一次完整的分析）

```
[Binance WS]      ──> 1m K线 + Trades + 爆仓流入
[Coinglass REST]  ──> 加权资金费率（每 10s）
       │
       ▼
[Polars 处理]     ──> 增量更新 CVD、Volume Profile、多周期对齐
       │
       ▼
[结构模块]        ──> 检测到 15m 出现 CHoCH（看涨）
[流动性模块]      ──> 检测到下方 Equal Lows 被插针扫荡
[VSA 模块]        ──> 15m + 1h 看涨背离
[OI 模块]         ──> 扫荡瞬间 OI 下降（空头平仓）
[Funding]         ──> 加权费率 -0.012%（极值附近，反向参考）
       │
       ▼
[聚合器]          ──> 生成情境报告，置信度 7.5/10
       │
       ▼
[告警]            ──> Telegram 推送 + Dashboard 高亮
       │
       ▼
[用户决策]        ──> 人来决定是否进场、仓位、止损
       │
       ▼
[日志]            ──> 记录交易 + 当时情境快照
```

---

## 七、技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 / 运行时 | **Python 3.11+** | 单 symbol 单所，并发不是瓶颈；分析逻辑迭代速度第一 |
| 包管理 | **uv** + `pyproject.toml` | 极快、现代、单一可信源 |
| 异步框架 | **asyncio** | 标准库，足够 |
| 交易所接入 | `python-binance` 或 `ccxt.pro` | Binance 现成支持 |
| HTTP 客户端 | `httpx`（异步） | Coinglass REST + 备份接口 |
| 数据处理 | **Polars** + NumPy | 比 pandas 快 5-10 倍，API 更现代 |
| 存储 | **DuckDB** | 单文件、零运维、SQL 直查 |
| Web 后端 | **FastAPI** + Uvicorn | 异步、轻量、自动文档 |
| Web 前端 | React + TypeScript + **TradingView Lightweight Charts** | 图表事实标准 |
| Telegram | `python-telegram-bot` | 推送告警 |
| CLI | `typer` | 类型友好 |
| 类型检查 | **mypy** + **ruff** | 弥补动态类型 |
| 测试 | `pytest` + `pytest-asyncio` | 单测 + 集成测 |
| 部署 | Docker（单容器） | 简洁起步 |
| 调度 | `APScheduler` 或 asyncio loop | 周期任务（OI 拉取等） |

**未来可选升级**：
- 数据规模 > 10GB → 迁移 ClickHouse
- 多交易所聚合需求 → 用 Go/Rust 重写 Ingestion 网关，通过 Redis Streams 接入
- 机器学习辅助形态识别 → PyTorch（仅作辅助评分，不替代规则）

---

## 八、开发路线图（Roadmap）

时间估算基于**单人兼职开发**，全职可显著压缩。

### **Phase 0 — 基础设施（第 1 周）**
- 项目骨架（`uv` + `pyproject.toml` + `ruff` + `mypy` + `pytest`）
- 配置管理（`pydantic-settings`，环境变量 + `.env`）
- 日志系统（`structlog`）
- DuckDB 初始化 + 表结构
- Binance WebSocket 接入：K 线 + Trades + 爆仓流
- Binance REST：历史 K 线补齐 + OI 轮询
- Coinglass REST 接入 + 自聚合降级方案
- 数据完整性校验

### **Phase 1 — 核心分析引擎（第 2-4 周）**
- Swing 检测 + BOS/CHoCH
- CVD / Delta / Volume Profile / VWAP（Polars 实现）
- Order Block / FVG 标注
- 简易 Web 图表（FastAPI + Lightweight Charts）+ 标注叠加

### **Phase 2 — 流动性引擎（第 5-6 周）** ⭐
- Equal Highs/Lows 识别
- Stop Hunt 检测算法（结合 Trades 数据）
- 爆仓热力图
- 量价背离检测（多周期）

### **Phase 3 — 上下文聚合 + 告警（第 7-8 周）**
- Wyckoff 阶段状态机
- 情境报告生成器
- Telegram 推送
- 告警规则 DSL（YAML 配置，用户可自定义组合条件）

### **Phase 4 — 复盘与回测（第 9-10 周）**
- K 线回放系统（前端拖拽时间轴）
- 交易日志 + 情境快照关联
- 历史规则回测（统计某种情境组合的后续表现）

### **Phase 5 — 打磨**
- 性能优化（必要时把热点模块用 Rust + PyO3 重写）
- 移动端适配（响应式 Web）
- 文档完善

---

## 九、目录结构

单 Python 项目，模块化但不过度工程：

```
Price-Action-Trading-Assistant/
├── docs/
│   ├── ARCHITECTURE.md         # 本文件
│   └── trading-theory/         # 各交易理论笔记（Wyckoff/SMC/ICT…）
├── pa_assistant/               # 主包
│   ├── __init__.py
│   ├── config.py               # 配置管理
│   ├── ingestion/              # 数据接入
│   │   ├── binance.py
│   │   ├── coinglass.py
│   │   └── funding_aggregator.py  # 多所自聚合（降级方案）
│   ├── storage/                # DuckDB 封装
│   │   ├── schema.py
│   │   └── repository.py
│   ├── processing/             # 数据处理
│   │   ├── timeframe.py        # 多周期对齐
│   │   ├── volume.py           # CVD/Delta/Profile
│   │   └── vwap.py
│   ├── analyzer/               # 分析引擎 ⭐
│   │   ├── structure.py        # BOS/CHoCH/Swing
│   │   ├── liquidity.py        # Order Block/FVG/Stop Hunt
│   │   ├── vsa.py              # 量价背离/VSA
│   │   ├── wyckoff.py          # Wyckoff 状态机
│   │   └── aggregator.py       # 上下文聚合器
│   ├── alerts/                 # 告警引擎
│   │   ├── rules.py
│   │   └── telegram.py
│   ├── journal/                # 交易日志
│   ├── api/                    # FastAPI 后端
│   │   ├── main.py
│   │   └── routers/
│   ├── replay/                 # 回放/复盘
│   └── cli.py                  # Typer 命令行
├── web/                        # React 前端
│   ├── src/
│   └── package.json
├── tests/
│   ├── unit/
│   └── integration/
├── scripts/                    # 一次性脚本（数据导出、回测）
├── data/                       # DuckDB 文件（git ignore）
├── .env.example
├── pyproject.toml              # uv + 项目配置
├── Dockerfile
├── docker-compose.yml          # 可选
└── README.md
```

---

## 十、风险与边界声明

1. **本系统不构成投资建议**。所有输出仅为辅助分析，最终决策由用户承担全部责任。
2. **过去的模式不保证未来重现**。流动性结构、Wyckoff 阶段都是概率性工具。
3. **避免过拟合**。开发过程中所有规则都需要在样本外数据上验证，警惕"在历史上完美"的陷阱。
4. **心理风险 > 技术风险**。再好的系统也救不了不止损、扛单、报复性交易的人。系统会内置交易日志强制复盘机制，但纪律仍需自律。
5. **API 依赖风险**。Coinglass 故障时降级到自聚合，Binance 故障时无法工作（这是聚焦带来的代价）。

---

## 十一、下一步

1. 确认本架构是否符合预期
2. 确认 Coinglass 套餐 / 是否先用自聚合方案起步
3. 搭建项目骨架（`uv init` + 基础目录 + 配置 + CI）
4. 实现 Phase 0 的 Binance 数据接入

> *"The market does not care about your indicators. It cares about liquidity."*
