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
│                        单 Python 进程                            │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    调度层 (Scheduler)                       │ │
│  │  APScheduler: 每天8:05日K / 每小时1H / 每4小时4H           │ │
│  │  自动拉取: K线回填 + OI快照 + 资金费率                      │ │
│  └──────────────────────────┬─────────────────────────────────┘ │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────────┐ │
│  │                    AI 分析层 (LLM)                          │ │
│  │  结构化数据 → Prompt → OpenAI 兼容 API → 中文分析报告       │ │
│  └──────────────────────────┬─────────────────────────────────┘ │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────────┐ │
│  │              分析引擎 (Analysis Engine) ⭐ 核心              │ │
│  │  ┌────────┬────────────┬────────┬────────┬──────────────┐ │ │
│  │  │ 结构   │ 流动性引擎  │  VSA   │ Wyckoff│ 上下文聚合器  │ │ │
│  │  │ Module │ Liquidity   │ Module │ FSM    │ Aggregator    │ │ │
│  │  └────────┴────────────┴────────┴────────┴──────────────┘ │ │
│  └──────────────────────────┬─────────────────────────────────┘ │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────────┐ │
│  │              数据处理 (Polars + NumPy)                      │ │
│  │  多周期对齐 │ 增量计算 │ CVD/Delta/VWAP/Volume Profile      │ │
│  └──────────────────────────┬─────────────────────────────────┘ │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────────┐ │
│  │                  存储层 (DuckDB 单文件)                      │ │
│  │       K 线 │ OI │ Funding │ 情境快照                         │ │
│  └──────────────────────────┬─────────────────────────────────┘ │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────────┐ │
│  │                    数据接入 (Ingestion)                      │ │
│  │  ┌─────────────────────┐    ┌─────────────────────────┐   │ │
│  │  │ Binance Connector   │    │ 5 源资金费率聚合         │   │ │
│  │  │ (REST)              │    │ (Binance+OKX+Bybit+     │   │ │
│  │  │ K线/OI              │    │  Bitget+Gate.io)        │   │ │
│  │  └─────────────────────┘    └─────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────────┐ │
│  │                    推送层 (Notifications)                    │ │
│  │  飞书 / Telegram / 企业微信                                  │ │
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
- **多周期派生与未收盘过滤**：只持久 1m K 线，更高 TF 通过 `resample_ohlcv()` 按需聚合。
  - 支持：3m / 5m / 15m / 30m / 1h / 2h / 4h / 6h / 8h / 12h / 1d / 1w，基于 Polars `group_by_dynamic`，保证多周期一致性。
  - **未收盘K线动态过滤器 (`_drop_incomplete_candle`)**：为彻底排除定时触发时“刚开盘仅数分钟的最新K线”对技术指标、Swing分形及 Wyckoff 状态机的噪音干扰，系统在重采样后立即进行实时 UTC 时间边界校验，若最后一根 K 线未完全闭合，则直接使用波段滑片将其剔除，确保所有分析均基于已完全沉淀的闭合蜡烛图。
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

#### 5.4.5 上下文聚合器（Context Aggregator）✅ 已实现

把上面所有模块的输出**聚合成一份"市场情境报告"**，这是系统的最终交付物。

**数据结构（全部 frozen-slots dataclass）**

| 类 | 内容 |
|---|---|
| `TrendContext` | HTF + 工作 TF 趋势方向 + 5 种对齐标签 |
| `WyckoffContext` | 当前 FSM 状态 + phase-aware 下一步提示 |
| `LiquidityMap` | 上方/下方未扫荡流动性池 + 最近被扫荡池 |
| `ZoneContext` | 生效中的 OB + FVG + 最近上方/下方区域 |
| `FlowContext` | CVD 趋势 + VWAP 距离 + POC + 近期背离列表 |
| `StopHuntContext` | 最近确认 stop hunt + bias 推断 |
| `FundingContext` | OI + 24h 变化 + 5 所加权资金费率 |
| `Scorecard` | bullish/bearish factor 列表 + 净 bias（+2 margin 防 flip-flop） |
| `ContextReport` | 以上全部 + 多空 invalidation 价位 + 最近磁吸位 |

**Scorecard 8 类规则**（纯函数，无魔法数字）

1. Wyckoff phase bias（ACC_A/B/C/D/E vs DIST_A/B/C/D/E）
2. 趋势对齐（aligned_bull / aligned_bear / 反向候选）
3. Stop hunt 方向（Spring → 看多，UTAD → 看空）
4. 背离（按 strength 阈值过滤，indicator-agnostic）
5. 生效中的 OB 数量（≥2 才计入）
6. 流动性磁吸（单侧未测试 pool 集中）
7. CVD 趋势（近 N 根 bar 方向）
8. 资金费率极值（contrarian，阈值 ±0.0003）

净 bias 判定：`len(bullish) - len(bearish) >= 2` 才翻多，`<= -2` 才翻空，否则中性。

**渲染器**

- `render_text()` — 终端友好，多 section 对齐格式（`pa context-report` 使用）
- `render_markdown()` — IM 平台 markdown，支持 `language="en"/"zh"` 双语切换（`pa send-alert` 使用中文）

**`pa send-alert` 推送流程**

```
backfill 数据 → 9 个分析模块 → 7 个子上下文 builder
→ build_context_report() → render_markdown(language="zh")
→ send_to_all([telegram, wechat_work, lark])
   asyncio.gather(return_exceptions=True)  # 单 channel 故障不影响其他
```

**这是系统的最终交付物。不是信号，是情境。**

### 5.5 调度层（Scheduler）

**职责**：定时触发数据拉取 + 分析 + 推送全流程。

#### APScheduler 配置与 K 线对齐定时器 (`CronTrigger`)

为保证分析引擎启动时蜡烛图刚好走完 5 分钟沉淀期，且避开整点交易所的 API 请求洪峰，系统全面升级为 **`CronTrigger`（收盘后延迟 5 分钟高精度对齐触发）**：

| 定时触发点 (北京时间) | 触发频率 | 策略分析周期 | 高周期参考 | 定时器机制 | 说明 |
|:---|:---|:---|:---|:---|:---|
| **每天 08:05** | 每天一次 | **1D（日K）** | 无 | `CronTrigger` | 宏观大周期研判，自动切除未收盘的今日线。 |
| **每小时的第 5 分钟** (如 13:05, 14:05...) | 每小时一次 | **1H（小时K）** | **4H** | `CronTrigger` | 日内短线与突破研判，自动切除未收盘的本小时线。 |
| **每 4 小时收盘后 5 分钟** (00/04/08/12/16/20:05) | 每4小时一次 | **4H（波段K）** | **1D** | `CronTrigger` | 中线波段策略分析，自动切除未收盘的本4小时线。 |

#### 并发安全互斥锁与数据去重机制 (`asyncio.Lock` + `Cache`)

在重合时间点（例如 08:05 或 20:05，1H/4H/1D 任务会同时触发），为避免重复拉取浪费 API 频次以及并发写入 DuckDB 导致连接冲突，系统设计了并发安全控制架构：
*   **并发写库互斥防撞**：引入全局异步锁 `_fetch_lock = asyncio.Lock()`。并发任务激活时，仅首个抢占锁的任务执行真实网络拉取和 DuckDB 写入，其余任务异步挂起等待，这彻底杜绝了并发读写造成的 DuckDB `ConnectionException` 锁库报错。
*   **30秒网络拉取防抖缓存**：先驱任务释放锁后，排队等待的任务依次被唤醒，但会先校验 `now - _last_fetch_time < 30.0`。若为真，表明最新数据已被并发任务安全存入本地，则**直接跳过重复的网络拉取与写入**，实现了 **0 次重复网络请求**，消存在触发交易所 429 限频的危险。

#### 自动数据拉取

每次调度触发时，自动执行：
1. 回填最近 1 天 K 线（Binance REST）
2. 更新 OI 快照
3. 更新资金费率（5 所持仓加权自聚合）

失败不阻断：任何一步失败只记录日志，不中断后续流程。

#### CLI 命令

```bash
pa schedule-start          # 启动定时调度器（后台运行）
pa ai-analyze --timeframe 1h  # 手动触发一次（自动拉取 + 分析 + 推送）
pa ai-analyze --timeframe 1h --no-fetch  # 跳过拉取，使用已有数据
pa ai-analyze --timeframe 1h --dry-run   # 试运行，只打印不推送
```

### 5.6 AI 分析层（LLM）与风控研判法则

**职责**：将结构化市场数据转化为高水准、具有风控意识的交易建议报告。

#### 升级角色预设 (System Prompt)
AI 角色设定从通用的分析师升级为 **“资深自营交易员与风险控制主管”**，使其具备敏锐的风险意识、多因子共振哲学，并坚决从数据中推演结论，剔除废话。

#### 两大核心交易研判法则

1.  **CVD / OI / 价格共振黄金法则**：
    在提示词中直接嵌入量价持仓共振公式，指导 AI 判定**真假突破**、**无量挤压反弹**和**多头踩踏清算**。并加入现货与费率的背离指引（`价升费平 = 现货健康拉升`；`价跌费升 = 杠杆抄底危险区`）。
2.  **宏观周期与资金燃料定律 (4H / 1D 专属)**：
    当 timeframe 为 4H 或 1D 时，动态为 AI 指引**杠杆燃料视角**：
    *   **牛市中的负费率** $\rightarrow$ 空头对冲盘是后市暴涨的 **上涨燃料 (Short Squeeze 燃料)**，指导 AI 逢低寻找 Spring 做多。
    *   **熊市中的正费率** $\rightarrow$ 散户死扛抄底是连环清算的 **下跌燃料 (Long Liquidation 燃料)**，指导 AI 逢高寻找 OB 阻力做空。

#### 设计原则与风控底线

- **OpenAI 兼容 API**：支持 OpenAI、DeepSeek（推荐，中文好）、本地模型等。
- **结构化 Prompt**：收集市场数据，格式化为结构化 prompt，自带 R:R（盈亏比最低要求 1:1.5）和双向情景规划（首选计划 + 失效后的反转备用计划）强力规范。
- **双语支持**：中文 / 英文输出。

#### LLM 配置（.env）

```bash
LLM_API_KEY=sk-your-key
LLM_BASE_URL=https://api.openai.com/v1  # 或 DeepSeek/其他兼容接口
LLM_MODEL=gpt-4o
LLM_MAX_TOKENS=2000

# 可选：分周期大模型最大生成 Token 限制
LLM_MAX_TOKENS_1H=1000
LLM_MAX_TOKENS_4H=1500
LLM_MAX_TOKENS_1D=3000
```

### 5.7 应用服务层

- **CLI 工具**（已实现 ✅）：基于 `typer`，覆盖数据接入 + 分析 + AI 推送全流程
- **告警引擎**（已实现 ✅）：`pa send-alert` 跑情境聚合报告 → Telegram/企微/飞书并发分发
- **定时推送**（已实现 ✅）：`pa schedule-start` 定时触发数据拉取 + AI 分析 + 推送

### 5.8 用户交互与推送层（User Interface & Notifications）

#### 5.8.1 CLI 交互工具
- 基于 `typer` 开发，提供类型友好且带自动帮助文档的命令行界面。
- 覆盖了数据回填、手动触发分析、启动定时调度器等全套系统运维操作。

#### 5.8.2 多渠道消息推送与并发分发
- **统一抽象**：定义了 `NotificationChannel` 协议，所有推送接口（飞书/Telegram/企业微信）均实现该协议。
- **并发分发机制**：通过 `asyncio.gather(..., return_exceptions=True)` 并发投递到所有已启用的推送渠道。这确保了单个渠道的网络超时或限频故障不会对其他渠道产生级联阻断。
- **失败降级**：优雅捕获和记录单个通道的投递异常，保证核心业务流程的连续性。

#### 5.8.3 分周期飞书机器人路由推送 (Timeframe-Specific Lark Routing) ⭐
为了解决多周期频繁推送造成的信息洪流，便于交易员对不同级别的行情研判进行分类监控，系统设计并实现了 **“分周期飞书机器人路由推送”** 机制：

*   **精细化路由配置**：在 `.env` 中独立支持以下周期专属的飞书 Webhook 变量：
    *   `LARK_WEBHOOK_URL_1H`：用于 1H（小时级）日内短线与突破研判报告。
    *   `LARK_WEBHOOK_URL_4H`：用于 4H（4小时级）中线波段策略分析报告。
    *   `LARK_WEBHOOK_URL_1D`：用于 1D（日K级）宏观大周期研判报告。
*   **动态覆写机制**：
    在 `run_analysis_job` 启动时，系统识别当前分析的 `timeframe`。若检测到配置了对应的 `LARK_WEBHOOK_URL_X` 专属 Webhook，系统将：
    1.  从已启用的全局通道列表中动态移除通用的全局飞书通道（`lark`）。
    2.  动态实例化一个全新的、指向专属周期 Webhook 的 `LarkChannel` 实例并追加至发送通道列表中。
*   **平滑优雅降级**：
    若当前周期未配置专属的 `LARK_WEBHOOK_URL_X`，系统将**自动降级**并复用全局 `LARK_WEBHOOK_URL` 变量进行标准推送。如果全局变量亦未配置，则静默跳过飞书通道推送，不影响 Telegram 或企业微信的并发投递，确保系统的极高稳健性。

---

## 六、数据流示例（一次完整的 AI 分析）

```
[定时调度触发]     ──> 每天 08:05 / 每小时 / 每 4 小时
        │
        ▼
[自动数据拉取]     ──> 回填 K 线 + 更新 OI + 更新资金费率
        │
        ▼
[分析引擎]        ──> 结构/流动性/量价/Wyckoff → 结构化数据
        │
        ▼
[LLM 分析]        ──> 结构化数据 → Prompt → OpenAI API → 报告
        │
        ▼
[推送]            ──> 飞书 / Telegram / 企业微信
        │
        ▼
[用户决策]        ──> 人来决定是否进场、仓位、止损
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
| 测试 | **pytest** + `pytest-asyncio` | 单测 + 集成测（当前 304 个） |
| 调度 | **APScheduler** | 定时任务，支持 cron 表达式和间隔触发 |
| LLM | **OpenAI 兼容 API** | 灵活切换 OpenAI / DeepSeek / 本地模型 |

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

### **Phase 3 — 上下文聚合 + 告警（4/4 切片完成 ✅）**
- ✅ **切片 1**：Wyckoff 阶段状态机（11 状态 + 12 事件 + 纯函数 FSM + confluence 评分）
- ✅ **切片 2**：完善事件检测器（AR/ST/SOS/LPS + 1H 闸控 + range 重锚）
- ✅ **切片 3**：情境聚合报告（7 子上下文 + Scorecard + render_text/markdown）
- ✅ **切片 4**：告警推送（Telegram / 企微 / 飞书，统一 Protocol + 并发分发）

### **Phase 4 — AI 分析 + 定时推送（已完成 ✅）**
- ✅ **切片 1**：LLM 分析模块（OpenAI 兼容 API，结构化 prompt，双语输出）
- ✅ **切片 2**：定时调度器（APScheduler，每天 8:05 日K / 每小时 1H / 每 4 小时 4H）
- ✅ **切片 3**：自动数据拉取（分析前自动回填 K 线 + 更新 OI + 更新资金费率）

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
│   ├── cli.py                  # typer CLI
│   ├── scheduler.py            # 定时调度器 + 自动数据拉取
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
│   │   ├── wyckoff.py          # Wyckoff 阶段状态机（FSM）
│   │   ├── context.py          # 情境聚合报告（7 子上下文 + Scorecard）
│   │   └── llm.py              # LLM 分析模块（OpenAI 兼容 API）
│   ├── notifications/          # 推送通道
│   │   ├── telegram.py         # Telegram Bot API
│   │   ├── wechat.py           # 企业微信群机器人
│   │   └── lark.py             # 飞书群自定义机器人
│   └── storage/                # 持久层
│       ├── schema.py           # DuckDB DDL
│       ├── repository.py       # Database 连接管理
│       └── writers.py          # 批量 upsert（幂等，Polars → Arrow）
├── tests/                      # 304 个单测
├── .env.example                # 环境变量模板
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

## 十一、当前状态

### 已完成

| Phase | 内容 | 测试 |
|---|---|---|
| 0 | 基础设施 + 5 源资金费率 + 代理 + 回填 | 117 |
| 1 | 分析引擎（结构/量价/订单块/FVG） | +64 |
| 2 | 流动性引擎（池/Stop Hunt/背离） | +65 |
| 3 | 上下文聚合 + Wyckoff + 告警推送 | +77 |
| 4 | AI 分析 + 定时调度 + 自动数据拉取 | +15 |
| **合计** | | **304** |

> *"The market does not care about your indicators. It cares about liquidity."*
