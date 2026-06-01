uv run pa ai-analyze --timeframe 4h --dry-run
2026-06-01T07:58:34.097362Z [info     ] ai_analyze_start               htf=None symbol=BTCUSDT timeframe=4h
正在从交易所拉取最新数据...
2026-06-01T07:58:35.015794Z [info     ] duckdb_connected               path=data\pa.duckdb
2026-06-01T07:58:35.021513Z [info     ] schema_initialized             version=1
HTTP Request: GET https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=1500&startTime=1780214314099&endTime=1780300714099 "HTTP/1.1 200 OK"
2026-06-01T07:58:37.231472Z [info     ] klines_upserted                rows=1440 symbol=BTCUSDT
2026-06-01T07:58:37.384586Z [info     ] duckdb_closed                  path=data\pa.duckdb
2026-06-01T07:58:37.386975Z [info     ] fetch_klines_done              symbol=BTCUSDT written=1440
2026-06-01T07:58:37.387097Z [info     ] fetch_oi_start                 symbol=BTCUSDT
HTTP Request: GET https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT "HTTP/1.1 418 I'm a teapot"
2026-06-01T07:58:39.077239Z [warning  ] binance_fetch_oi_failed_trying_bybit_fallback error="Client error '418 I'm a teapot' for url 'https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/418"
HTTP Request: GET https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min&limit=1 "HTTP/1.1 200 OK"
2026-06-01T07:58:40.863543Z [info     ] duckdb_connected               path=data\pa.duckdb
2026-06-01T07:58:40.868640Z [info     ] schema_initialized             version=1
2026-06-01T07:58:40.897527Z [info     ] oi_snapshot_written            open_interest=53774.043 symbol=BTCUSDT
2026-06-01T07:58:40.992236Z [info     ] duckdb_closed                  path=data\pa.duckdb
2026-06-01T07:58:40.992371Z [info     ] fetch_oi_done                  oi=53774.043 symbol=BTCUSDT
2026-06-01T07:58:40.992462Z [info     ] fetch_funding_start            symbol=BTCUSDT
2026-06-01T07:58:40.992556Z [info     ] funding_provider_selected      provider=self_aggregated
HTTP Request: GET https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP "HTTP/1.1 200 OK"
HTTP Request: GET https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol=BTCUSDT&productType=USDT-FUTURES "HTTP/1.1 200 OK"
HTTP Request: GET https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=1 "HTTP/1.1 200 OK"
HTTP Request: GET https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min&limit=1 "HTTP/1.1 200 OK"
HTTP Request: GET https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT "HTTP/1.1 418 I'm a teapot"
HTTP Request: GET https://api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT "HTTP/1.1 200 OK"
HTTP Request: GET https://api.bitget.com/api/v2/mix/market/open-interest?symbol=BTCUSDT&productType=USDT-FUTURES "HTTP/1.1 200 OK"
HTTP Request: GET https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP "HTTP/1.1 200 OK"
2026-06-01T07:58:46.940884Z [warning  ] exchange_fetch_failed          error=HTTPStatusError exchange=binance message="Client error '418 I'm a teapot' for url 'https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT'\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/418"
2026-06-01T07:58:46.941046Z [info     ] funding_aggregated             sample_count=4 sources=['okx', 'bybit', 'bitget', 'gateio'] symbol=BTCUSDT weighted_rate=5.924011530798539e-05
2026-06-01T07:58:46.961383Z [info     ] duckdb_connected               path=data\pa.duckdb
2026-06-01T07:58:46.966266Z [info     ] schema_initialized             version=1
2026-06-01T07:58:47.005326Z [info     ] funding_weighted_written       source=self_aggregated symbol=BTCUSDT weighted_rate=5.924011530798539e-05
2026-06-01T07:58:47.099935Z [info     ] duckdb_closed                  path=data\pa.duckdb
2026-06-01T07:58:47.100087Z [info     ] fetch_funding_done             rate=5.924011530798539e-05 source=self_aggregated symbol=BTCUSDT
数据拉取完成
Calling LLM (mimo-v2.5-pro)...
2026-06-01T07:58:47.133789Z [info     ] llm_request                    base_url=https://token-plan-cn.xiaomimimo.com/v1 model=mimo-v2.5-pro prompt_len=2125

Title: [BTCUSDT 4H] AI 分析报告

## 交易分析报告：BTCUSDT (4H)

**核心结论：** 市场处于高位 **分布（Distribution）** 阶段，工作周期与高周期共振向下。量价背离信号密集，卖方主导订单流。价格正被关键流动性磁吸位吸引，交易计划应以 **逢高做空** 为主。

---

### 1. 趋势与结构共振分析
- **Wyckoff 结构**：处于 **distribution_phase_b**（置信度74%），这是主力在高位派发筹码的阶段。当前价格 **73,726.20** 位于区间 **72,556 - 78,077** 的下半部，但尚未有效跌破支撑。
- **多周期共振**：工作周期（4H）趋势为 **下跌**。**高周期趋势一致性为“无”**，表明当前级别可能独立运行一段趋势，但宏观缺乏明确方向支持，需高度警惕区间内的反复。
- **关键信号**：价格处于 Phase B/C 的震荡末期，向下测试支撑的意图明显。未出现明确的 Phase D 上涨确认信号。

### 2. 订单流与量价动力解读
- **CVD（累积成交量差）**：**-4900**，卖方显著主导市场，表明主动卖出压力持续。
- **持仓量（OI）**：**-0.10%**（24h），轻微下降。结合价格低位和 CVD 下跌，符合 **【价格下跌 + OI减少 + CVD减少】** 的混合模式，指向 **多头止损/爆仓驱动的被动流失**，而非强力空头建仓。这通常预示下跌动能可能减弱，但并非反转信号。
- **资金费率**：**+0.0059%**，处于温和正值。结合高周期趋势不明确，不构成极端看涨的“燃料”信号。资金费率未过热，短期反向洗盘风险较低。
- **背离信号**：出现多组 **看跌背离**（Volume、OI、CVD），其中 Volume 背离强度达59%，这是价格上行乏力、卖压暗涌的强烈警告。
- **共振结论**：价格、CVD、背离信号形成 **看跌共振**。但 OI 下降表明下跌主要由被动平仓驱动，需警惕在关键支撑位出现超跌反弹，但结构上仍偏向空头。

### 3. 流动性磁吸与关键价位
- **上行失效点**：**$78,077**。收盘价有效站上此位置，将彻底破坏分布结构，空头逻辑失效。
- **最近磁吸位（上行）**：**$74,184**（等高流动性池，2x）。此为最近的流动性聚集区，价格有极大概率向上扫清此处止损后再度下跌。这是计划中的 **首选做空入场区域**。
- **关键阻力**：
    1.  **FVG（公允价值缺口）**：**$74,565 - $74,632**。此为磁吸位之上的第一道结构阻力。
    2.  **订单块（OB）**：**$76,685 - $77,154**。核心卖压区域，若价格强力反弹至此，将提供盈亏比极佳的做空机会。
- **下行目标**：区间底部 **$72,556**。若有效跌破，将开启向更低流动性区域的下行空间。

---

### 4. 宏观周期与资金燃料定律
- **定律应用**：由于高周期趋势一致性为“无”，且资金费率仅为温和正值，**不适用** 强烈的“牛市蓄力”或“熊市诱多”定律。当前市场驱动力更偏向技术结构（分布阶段）与微观订单流。

### 5. 交易策略与风险管理

**仓位与风控**：Wyckoff 阶段置信度中等，且缺乏高周期共振，建议使用 **常规或保守仓位**。入场必须严格等待价格进入预设的“高胜算区域”。

#### 首选方案（主计划）：流动性磁吸位做空
- **核心逻辑**：价格向上测试磁吸位 **$74,184**，完成流动性收割，在上方 FVG 或订单块阻力处遇阻，顺势做空。
- **入场区间**：**$74,180 - $74,630**。分两批介入：
    1.  价格触及 **$74,184** 附近并出现滞涨 K 线形态（如 pin bar, engulfing bearish）时轻仓试空。
    2.  价格反弹至 **$74,565 - $74,632** FVG 区域并确认阻力有效时加仓。
- **止损位**：**$75,050**。设置在 FVG 阻力及前一个波段高点之上，确保结构逻辑未被破坏。
- **分批止盈目标**：
    -  **TP1**：**$72,900**（盈亏比约 1:1.8）
    -  **TP2**：**$72,556**（区间底部，盈亏比约 1:2.2）
    -  **TP3**：**$71,800**（结构破位后延伸目标）
- **盈亏比**：以入场中位价 $74,400 计算，至 TP1 的盈亏比 > **1:1.5**，满足要求。

#### 备选方案（反转计划）：结构性破位追空
- **触发条件**：价格未测试 $74,184 磁吸位，直接强势下跌并 **4H 收盘有效跌破 $72,556**。
- **操作**：回踩破位点（约 $72,550）入场做空，止损设于 $73,500 之上。
- **目标**：$71,000 及以下。

#### 无效化情境与纪律
- **绝对无效化**：任何 **4H 收盘价 > $78,077**。立即止损所有空单，并观望，因分布结构可能被证伪。
- **交易纪律**：本计划为 **波段交易**，预期持仓时间为数天。必须严格执行止损，不逆势抄底。在价格触及磁吸位前，保持观望。

**最终建议**：**观望等待，计划于 $74,180-$74,630 区域做空。** 当前价格位于尴尬的中间位置，不具备高确定性入场点。耐心等待价格运行至流动性磁吸区是优选策略。