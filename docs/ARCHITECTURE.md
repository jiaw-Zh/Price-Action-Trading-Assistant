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

| 数据 | 来源 | 接入方式 | 用途 | 状态 |
|---|---|---|---|---|
| K 线 OHLCV | **Binance Futures** | REST（历史回填） | 价格结构基础 | ✅ 已实现 |
| 持仓量（OI） | **Binance Futures** | REST 轮询（1 分钟） | 加仓/减仓判断 | ✅ 已实现 |
| **OI 加权资金费率** | **5 源自聚合**（Binance + OKX + Bybit + Bitget + Gate.io） | REST 并行拉取 | 多空杠杆情绪极值 | ✅ 已实现 |
| K 线实时流 | **Binance Futures** | WebSocket | 实时更新 | ⏳ 推迟 |
| 逐笔成交（Trades） | **Binance Futures** | WebSocket | CVD 精确计算、主动买卖 | ⏳ 推迟 |
| 爆仓流（Liquidations） | **Binance Futures** | WebSocket（forceOrder） | 流动性猎杀确认 | ⏳ 推迟 |
| 多空账户比 | **Binance Futures** | REST 轮询（5 分钟） | 散户立场参考 | ⏳ 未实现 |

> **为什么不用 Coinglass？**
> Coinglass 付费 API 价格较高，且网页端数据经过加密无法抓取。
> 实测发现 5 源自聚合（按 OI 加权）的结果与 Coinglass 方向一致，
> 精度足够用于极值判断。`FundingProvider` Protocol 保留了未来
> 接入 Coinglass 付费 API 的能力，零代码切换。

> **为什么 K 线只用 Binance？**
> 多交易所聚合 K 线会产生虚假的 BOS/CHoCH 信号（不同所的 wick
> 不同导致 swing 判断不一致）。单源保证结构分析的确定性。

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

**职责**：稳定地获取数据，封装多个交易所 Connector。

#### 共享基类 `_http.py`
- 基于 `httpx.AsyncClient` 的异步 HTTP 基类
- 内置指数退避重试（可配置次数 + 间隔）
- 统一代理注入（`HTTP_PROXY_URL` 环境变量，支持 HTTP/SOCKS5）
- 所有子类继承此基类，无需重复处理网络细节

#### Binance Connector（已实现 ✅）
- REST：历史 K 线批量回填、OI 快照轮询
- 仅使用公开 API（无需 API Key）
- 代理必须配置（大量 VPS IP 被 CDN 封锁）

#### 多所资金费率聚合（已实现 ✅）
- `FundingProvider` Protocol 抽象
- 5 个实现：Binance / OKX / Bybit / Bitget / Gate.io
- `asyncio.gather(return_exceptions=True)` 并行拉取
- 按各所 OI 加权计算全市场资金费率
- 任何 1-2 源失败自动降级（不影响整体）
- Coinglass 付费 API 留 stub，未来可零代码切换

#### WebSocket（⏳ 推迟）
- K 线 / aggTrade / forceOrder 流
- 待定时任务基础完成后再接入

### 5.2 存储层（Storage）

**DuckDB 单文件**，分表存储：

| 表名 | 内容 | 主键 |
|---|---|---|
| `kline_1m` | 1 分钟 K 线（其他周期实时聚合） | `(symbol, open_time)` |
| `oi_1m` | 持仓量快照（5m 历史回填 + 实时轮询） | `(symbol, timestamp)` |
| `funding_weighted` | 5 源 OI 加权资金费率 | `timestamp` |
| `trades` | 逐笔成交（按天分区） | `trade_id` |
| `liquidations` | 爆仓事件 | `timestamp + side` |
| `context_snapshots` | 情境报告历史（用于复盘） | `timestamp` |
| `journal` | 交易日志 | `trade_id` |

DuckDB 优势：零运维、单文件、SQL 直接查、Polars 原生互通。
未来数据量上 GB 级再考虑 ClickHouse。

### 5.3 数据处理层（Processing / Analysis）

实际实现为 `pa_assistant/analysis/` 包，纯函数，无 IO。

- **统一使用 Polars**（`polars-lts-cpu`，兼容无 AVX2 环境）
- **多周期派生**：只持久 1m K 线，更高 TF 通过 `resample_ohlcv()` 按需聚合
  - 支持：3m / 5m / 15m / 30m / 1h / 2h / 4h / 6h / 8h / 12h / 1d / 1w
  - 基于 Polars `group_by_dynamic`，保证多周期一致性
- **已实现的派生数据**：
  - **Delta per bar** = `2 * taker_buy_base - volume`（主动买卖差）
  - **CVD**（Cumulative Volume Delta）= `cumsum(delta)`
  - **VWAP** = `cumsum(quote_volume) / cumsum(volume)`（真实成交额，非 typical 近似）
  - **VWAP sigma 通道**：基于 `E[p^2|v] - vwap^2` 的标准差近似
  - **Volume Profile**：按 [low, high] 范围比例分配成交量到 n_bins 个价格格
  - **POC / VAH / VAL**：市场剖面算法（从 POC 向外扩展至覆盖 70% 成交量）

### 5.4 分析引擎（Analysis Engine）— 系统的大脑

#### 5.4.1 市场结构模块（Structure）

- Swing High / Swing Low 检测（基于分形或 ZigZag）
- **BOS（Break of Structure）**：趋势延续确认
- **CHoCH（Change of Character）**：趋势反转早期信号
- HH/HL vs LH/LL 序列追踪
- Range Detection：横盘区间识别（Wyckoff 阶段判断的前提）

#### 5.4.2 流动性引擎（Liquidity Engine）⭐ 核心

合约交易的本质就是流动性博弈，这是整个系统最关键的部分。

- **Order Block（订单块）** ✅ 已实现
  - BOS/CHoCH 之前的最后一根反向 K 线
  - 记录 body（保守入场）和 wick（宽松入场）两个范围
  - mitigation 跟踪：首次价格回踩 body 即标记失效
  - lookback 可配置（默认 10 根）

- **FVG（Fair Value Gap，公允价值缺口）** ✅ 已实现
  - 三根 K 线形成的失衡区（纯几何，独立于结构事件）
  - 区分 Bullish / Bearish FVG
  - mitigation 跟踪：首次任意 K 线触碰 gap 即标记

- **流动性池识别（Liquidity Pools）** ✅ 已实现
  - Equal Highs / Equal Lows（散户止损密集区）
  - 贪心 1-D 价格聚类（bps 容差可配置）
  - Sweep 跟踪：记录池被扫荡的时间和方式

- **Stop Hunt / Liquidity Sweep 检测** ✅ 已实现
  - 模式：快速插针突破 → 成交量放大 → 价格迅速收回
  - 三维置信度：wick_ratio + volume_ratio + confirmed（后续 N 根 bar 收回）
  - 区分 fakeout（收回）vs clean break（真突破）

- **多指标背离检测** ✅ 已实现
  - CVD / Volume / OI 三指标 indicator-agnostic 统一抽象
  - 邻近同类 swing 比较（HH-HH / LL-LL）
  - 归一化 strength 0..1
  - 缺失指标列静默跳过（OI 没回填时 graceful 降级）

- **爆仓热力图** ⏳ 待 WebSocket forceOrder 流接入
  - 基于 OI + 价格结构估算多空爆仓密集区
  - 与价格结构叠加，标注"磁吸位"

#### 5.4.3 量价分析模块（VSA）

核心思想：**努力 vs 结果**（Effort vs Result）

- **量价背离检测** ✅ 已实现
  - 价格新高，CVD/Delta 不创新高 → 看跌背离
  - 价格新低，CVD/Delta 不创新低 → 看涨背离
  - OI 背离：价格新高但 OI 下降 → 空头平仓推动假涨
  - 三指标 indicator-agnostic 统一抽象，归一化 strength

- **Volume Climax** ✅ 已实现（集成在 Wyckoff 检测器中）
  - 基于 rolling z-score 的异常放量检测
  - 与 swing 极值 + 拒绝影线组合 → SC/BC 事件

- **No Demand / No Supply Bar**：低量小阳/小阴（趋势衰竭）— 集成在 ST 检测中
- **Effort vs Result 异常**：大量但价格不动 → 主力吸收 — 集成在 Wyckoff Phase B 判定中

#### 5.4.4 Wyckoff 阶段状态机（FSM）✅ 已实现

- **11 个状态**：NEUTRAL + ACC_A..E + DIST_A..E
- **12 种事件**：SC/AR/ST/SPRING/SOS/LPS（吸筹）+ BC/AR_DIST/ST_DIST/UTAD/SOW/LPSY（派发）
- **6 层检测 pass**：
  1. Climaxes（SC/BC）— volume z-score + swing + rejection wick
  2. Springs/UTADs — 复用 stop_hunt 模块（1H+ 时间框架闸控）
  3. AR/AR_DIST — climax 后首个显著反向 swing
  4. ST/ST_DIST — range 内回测 climax 价位 + volume 萎缩
  5. SOS/SOW — Spring 后突破 range + 高量大实体
  6. LPS/LPSY — SOS 后 swing holds 反转后的支撑位
- **纯函数 FSM**：`evolve(state, event) → state`，可回放可测试
- **多因子 confluence**：每个事件带 dict 分解（volume_climax / wick_rejection / pool_quality / divergence 等）
- **周期翻转规则**：Phase A/B 时高置信度反向 climax 翻转 cycle；Phase C+ 锁定
- **Range 重锚**：Phase B 内更低 SC / 更高 AR 自动更新 range 边界

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

- **CLI 工具**（已实现 ✅）：基于 `typer`，14 个命令覆盖数据接入 + 分析全流程
- **实时监控（Live Watch）**：图表实时叠加所有标注（OB、FVG、Liquidity、Wyckoff 阶段）— ⏳ 待做
- **回放/复盘（Replay）**：按任意时间点回放，逐 K 线推进，验证系统判断与实际走势 — ⏳ 待做
- **告警引擎（Alerts）**：基于"情境组合"触发（例如：CHoCH + 流动性扫荡 + 量价背离 同时成立）— ⏳ Phase 3
- **交易日志（Journal）**：手动记录交易，系统自动关联当时的市场情境快照，用于事后复盘 — ⏳ Phase 4

### 5.6 用户交互层

- **CLI 工具**（已实现 ✅）：基于 `typer`，14 个命令覆盖数据接入 + 分析全流程
- **推送渠道**（Phase 3）：Telegram / 企业微信 / 飞书 — 配置占位已预留
- **Web Dashboard**（暂跳过）：FastAPI + TradingView Lightweight Charts — 需要时再做

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
| HTTP 客户端 | **httpx**（异步 + SOCKS5 支持） | 所有交易所 REST 统一用 |
| 数据处理 | **Polars**（`polars-lts-cpu`） | 比 pandas 快 5-10 倍，API 更现代；lts-cpu 兼容无 AVX2 环境 |
| 存储 | **DuckDB** | 单文件、零运维、SQL 直查、Polars Arrow 原生互通 |
| 配置 | **pydantic-settings** | 类型安全、`.env` 自动加载、`SecretStr` 脱敏 |
| 日志 | **structlog** | 结构化 JSON 日志，开发时 pretty-print |
| CLI | **typer** | 类型友好、自动帮助文档 |
| 类型检查 | **mypy strict** + **ruff** | 弥补动态类型，lint 统一 |
| 测试 | **pytest** + `pytest-asyncio` | 单测 + 集成测（当前 262 个） |

**当前未使用但保留规划**：
- Web 后端：FastAPI + Uvicorn（Phase 1 切片 4 暂跳过）
- Web 前端：TradingView Lightweight Charts
- 推送：Telegram / 企业微信 / 飞书（Phase 3）
- 部署：Docker（单容器）
- 调度：APScheduler 或 asyncio 常驻循环

**未来可选升级**：
- 数据规模 > 10GB → 迁移 ClickHouse
- 热点模块 → Rust + PyO3 重写
- 机器学习辅助形态识别 → PyTorch（仅作辅助评分，不替代规则）

---

## 八、开发路线图（Roadmap）

时间估算基于**单人兼职开发**，全职可显著压缩。

### **Phase 0 — 基础设施（已完成 ✅）**
- ✅ 项目骨架（`uv` + `pyproject.toml` + `ruff` + `mypy strict` + `pytest`）
- ✅ 配置管理（`pydantic-settings`，环境变量 + `.env`）
- ✅ 日志系统（`structlog`）
- ✅ DuckDB 初始化 + 表结构
- ✅ Binance REST：历史 K 线补齐 + OI 轮询 + OI 历史回填
- ✅ **5 源自聚合资金费率**（Binance + OKX + Bybit + Bitget + Gate.io，OI 加权）
- ✅ HTTP/SOCKS 代理支持（应对 CDN 区域封锁）
- ⏳ Binance WebSocket（K 线 / Trades / 爆仓流）— 推迟，REST 轮询暂时够用

### **Phase 1 — 核心分析引擎（3/4 切片完成 ✅）**
- ✅ **切片 1**：1m → 任意 TF Polars 重采样；分形 swing；BOS/CHoCH 状态机
- ✅ **切片 2**：Per-bar Delta + 累计 CVD；VWAP + σ 通道；Volume Profile (POC/VAH/VAL)
- ✅ **切片 3**：Order Block（依赖结构事件）+ Fair Value Gap（纯几何）+ mitigation 跟踪
- ⏭️ **切片 4**（Web 图表叠加）— 暂跳过，CLI 报告已能读

### **Phase 2 — 流动性引擎（3/4 切片完成 ✅）** ⭐
- ✅ **切片 1**：Equal Highs/Lows 流动性池识别（贪心 1-D 聚类 + sweep 跟踪）
- ✅ **切片 2**：Stop Hunt 检测（fakeout vs clean break，三维置信度）
- ✅ **切片 3**：多指标背离（CVD/Volume/OI）+ OI 历史回填基础设施
- ⏳ **切片 4**：爆仓热力图（待 WebSocket forceOrder 流接入）

### **Phase 3 — 上下文聚合 + 告警（2/4 切片完成 🚧）**
- ✅ **切片 1**：Wyckoff 阶段状态机（11 状态 + 12 事件 + 纯函数 FSM + confluence 评分）
- ✅ **切片 2**：完善事件检测器（AR/ST/SOS/LPS + 1H 闸控 + range 重锚）
- ⏳ **切片 3**：情境聚合报告（合并所有模块输出为一份可读决策报告）
- ⏳ **切片 4**：告警推送（企微 / 飞书 / Telegram）

### **Phase 4 — 复盘与回测**
- K 线回放系统
- 交易日志 + 情境快照关联
- 历史规则回测（统计某种情境组合的后续表现）

---

## 九、目录结构

```
Price-Action-Trading-Assistant/
├── docs/
│   └── ARCHITECTURE.md         # 本文件
├── pa_assistant/               # 主包
│   ├── __init__.py
│   ├── config.py               # pydantic-settings 配置管理
│   ├── logging.py              # structlog 封装
│   ├── cli.py                  # typer CLI（14 个命令）
│   ├── ingestion/              # 数据接入层（无分析逻辑）
│   │   ├── _http.py            # 共享 async HTTP 基类（重试 + 代理）
│   │   ├── binance.py          # Binance Futures REST + OI 历史迭代器
│   │   ├── okx.py              # OKX V5 REST
│   │   ├── bybit.py            # Bybit V5 REST
│   │   ├── bitget.py           # Bitget V2 Mix REST
│   │   ├── gateio.py           # Gate.io Futures V4 REST
│   │   └── funding.py          # FundingProvider Protocol + 5 源自聚合
│   ├── analysis/               # 纯函数分析层（无 IO，只接受 Polars DF）
│   │   ├── resample.py         # 1m → 任意 TF (group_by_dynamic)
│   │   ├── structure.py        # 分形 swing + BOS/CHoCH 状态机
│   │   ├── volume.py           # Delta / CVD / VWAP + sigma 通道
│   │   ├── profile.py          # Volume Profile (POC / VAH / VAL)
│   │   ├── zones.py            # Order Block + FVG + mitigation 跟踪
│   │   ├── liquidity.py        # Equal Highs/Lows 流动性池
│   │   ├── stop_hunt.py        # Stop Hunt / 流动性扫荡检测
│   │   ├── divergence.py       # 多指标背离（CVD/Volume/OI）
│   │   └── wyckoff.py          # Wyckoff 阶段状态机（FSM）
│   └── storage/                # 持久层
│       ├── schema.py           # DuckDB DDL
│       ├── repository.py       # Database 连接管理
│       └── writers.py          # 批量 upsert（幂等，Polars → Arrow）
├── tests/
│   ├── conftest.py             # 环境隔离 fixture
│   └── unit/                   # 262 个单测
│       ├── test_binance.py
│       ├── test_funding_aggregator.py
│       ├── test_okx_bybit_rest.py
│       ├── test_proxy.py
│       ├── test_writers.py
│       ├── test_resample.py
│       ├── test_structure.py
│       ├── test_volume.py
│       ├── test_profile.py
│       ├── test_zones.py
│       ├── test_liquidity.py
│       ├── test_stop_hunt.py
│       ├── test_divergence.py
│       └── test_wyckoff.py
├── data/                       # DuckDB 文件（.gitignore）
├── .env.example                # 环境变量模板（含推送渠道占位）
├── pyproject.toml              # uv 项目配置 + 依赖
├── Makefile                    # make check = lint + typecheck + test
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

## 十一、当前状态与下一步

### 已完成

| Phase | 内容 | 测试 |
|---|---|---|
| 0 | 基础设施 + 5 源资金费率 + 代理 + 回填 | 117 |
| 1.1 | 多周期重采样 + swing + BOS/CHoCH | +16 |
| 1.2 | Delta/CVD + VWAP + Volume Profile | +29 |
| 1.3 | Order Block + FVG + mitigation | +19 |
| 2.1 | Equal Highs/Lows 流动性池 | +18 |
| 2.2 | Stop Hunt 检测 | +20 |
| 2.3 | 多指标背离（CVD/Volume/OI）+ OI 回填 | +27 |
| 3.1 | Wyckoff 阶段状态机（11 状态 + 12 事件） | +28 |
| 3.2 | 完善事件检测器（AR/ST/SOS/LPS + 1H 闸控） | +7 |
| **合计** | | **262** |

### 下一步优先级

1. **Phase 3 切片 3 — 情境聚合报告**：合并所有模块输出为一份可读决策报告
2. **Phase 3 切片 4 — 告警推送**：企微 / 飞书 / Telegram
3. **定时任务 / 常驻服务**：让数据自动持续更新（当前需手动 backfill）
4. **Phase 4 — 复盘与回测**

> *"The market does not care about your indicators. It cares about liquidity."*
