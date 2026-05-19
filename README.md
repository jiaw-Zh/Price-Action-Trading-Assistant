# Price Action Trading Assistant

一个面向比特币合约交易者的**价格行为辅助决策系统**。

不抓新闻、不堆指标、不自动下单。
只做一件事：**把市场结构、流动性、量价关系，翻译成人能读懂的「市场情境报告」。**

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

### ✅ Phase 1 — 分析引擎（3/4 切片完成，Web UI 跳过）

| 切片 | 模块 | 内容 |
|---|---|---|
| ✅ 1 | `analysis/resample` + `analysis/structure` | 1m → 任意 TF 重采样；分形 swing 识别；BOS / CHoCH 事件检测 |
| ✅ 2 | `analysis/volume` + `analysis/profile` | Per-bar Delta / 累计 CVD / VWAP + σ 通道 / Volume Profile (POC / VAH / VAL) |
| ✅ 3 | `analysis/zones` | Order Block 识别（依赖结构事件）+ Fair Value Gap 识别（纯几何） + mitigation 跟踪 |
| ⏭️ 4 | Web UI | **暂跳过** — CLI 已能给出完整文字报告 |

### ✅ Phase 2 — 流动性引擎（3/4 切片完成）

| 切片 | 模块 | 内容 |
|---|---|---|
| ✅ 1 | `analysis/liquidity` | Equal Highs / Equal Lows 流动性池识别（贪心 1-D 聚类 + sweep 跟踪） |
| ✅ 2 | `analysis/stop_hunt` | Stop Hunt / 流动性扫荡检测（fakeout vs clean break，三维置信度） |
| ✅ 3 | `analysis/divergence` | 多指标背离（CVD / Volume / OI），indicator-agnostic 统一抽象 |
| ⏳ 4 | 爆仓热力图 | 待 WebSocket forceOrder 流接入 |

### 🚧 Phase 3 — 上下文聚合（2/4 切片完成）

| 切片 | 模块 | 内容 |
|---|---|---|
| ✅ 1 | `analysis/wyckoff` | Wyckoff 阶段状态机（11 状态 + 12 事件类型 + 纯函数 FSM） |
| ✅ 2 | `analysis/wyckoff` | 完善事件检测器（AR/ST/SOS/LPS + 1H 闸控 + range 重锚） |
| ⏳ 3 | 情境聚合报告 | 合并所有模块输出为一份可读决策报告 |
| ⏳ 4 | 告警推送 | 企微 / 飞书 / Telegram |

### ⏳ Phase 4 — 复盘与回测（未开始）

---

## 快速开始

```bash
# 1. 安装依赖
uv sync --extra dev

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env：HTTP_PROXY_URL=http://127.0.0.1:7890

# 3. 验证交易所连通性
uv run pa check-proxy

# 4. 初始化 DuckDB
uv run pa init-db

# 5. 回填数据
uv run pa backfill --days 7          # 7 天 1m K 线（约 10k 条）
uv run pa backfill-oi --days 7       # 7 天 5m OI 历史（约 2k 条）

# 6. 拉一次资金费率快照
uv run pa poll-funding

# 7. 跑分析
uv run pa analyze-structure --timeframe 1h --last 10
uv run pa analyze-volume --timeframe 1h
uv run pa analyze-zones --timeframe 1h
uv run pa analyze-liquidity --timeframe 1h
uv run pa analyze-stop-hunts --timeframe 1h
uv run pa analyze-divergences --timeframe 4h
uv run pa wyckoff --timeframe 1h

# 全套质量检查
make check        # = lint + typecheck + test (262 tests)
```

### 实际输出示例

```
$ uv run pa wyckoff --timeframe 1h
BTCUSDT  1h  current price: $76,706.70

  Wyckoff state: Accumulation Phase B  (confidence 67%)
  Range:         $76,666  -  $77,758

  Event chain (last 3):
    2026-05-17 23:00  Selling Climax (SC)          @$    76,666  conf 80%
    2026-05-18 12:00  Automatic Rally (AR)         @$    77,758  conf 52%
    2026-05-18 15:00  Secondary Test (ST)          @$    76,014  conf 49%

$ uv run pa analyze-divergences --timeframe 4h
BTCUSDT  4h  current price: $76,706.70
  Divergences  (6 total: 1 cvd, 3 volume, 2 oi)

    2026-05-15 12:00  ▲ bullish reversal    cvd      strength 100%
    2026-05-15 12:00  ▲ bullish reversal    volume   strength 58%
    2026-05-15 12:00  ▲ bullish reversal    oi       strength 4%
    ...
```

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

---

## CLI 命令一览

| 命令 | 用途 |
|---|---|
| `pa init-db` | 初始化 DuckDB schema |
| `pa show-config` | 打印当前生效配置（密钥自动脱敏） |
| `pa check-proxy` | 并行 ping 三家交易所诊断网络 |
| `pa backfill --days N` | 回填 N 天历史 1m K 线 |
| `pa backfill-oi --days N --period 5m` | 回填 N 天 OI 历史（5m/15m/.../1d） |
| `pa poll-oi` | 一次性 OI 快照 |
| `pa poll-funding` | 一次性多所加权资金费率 |
| `pa analyze-structure --timeframe TF` | Swing + BOS/CHoCH 事件报告 |
| `pa analyze-volume --timeframe TF` | Delta/CVD + VWAP + Volume Profile |
| `pa analyze-zones --timeframe TF` | Order Block + FVG 当前生效区列表 |
| `pa analyze-liquidity --timeframe TF` | Equal Highs/Lows 流动性池 |
| `pa analyze-stop-hunts --timeframe TF` | Stop Hunt 检测 |
| `pa analyze-divergences --timeframe TF` | CVD/Volume/OI 背离 |
| `pa wyckoff --timeframe TF` | Wyckoff 阶段状态机 |

所有 `analyze-*` / `wyckoff` 命令支持 `--timeframe`（5m / 15m / 1h / 4h / 1d 等）和 `--symbol` 覆盖。

---

## 目录结构

```
pa_assistant/
├── config.py                # pydantic-settings
├── logging.py               # structlog 封装
├── cli.py                   # typer 命令行（14 个命令）
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
│   └── wyckoff.py           # Wyckoff 阶段状态机（FSM）
└── storage/                 # 持久层
    ├── schema.py            # DuckDB DDL
    ├── repository.py        # Database 连接管理
    └── writers.py           # 批量 upsert（幂等）

tests/                       # pytest 单测（262 个）
docs/                        # 设计文档
```

---

## 设计原则

1. **`ingestion/` 与 `analysis/` 完全解耦** — 分析层不知道数据从哪来，只接受 Polars DataFrame
2. **持久只存 1m K 线 + 5m OI**，更高 TF 通过 `resample_ohlcv()` 按需派生
3. **抽象优先于实现** — `FundingProvider` Protocol 让 Coinglass / 自聚合 / 未来其他源零代码切换
4. **失败可降级** — 单交易所故障不影响整体（asyncio.gather + 部分成功语义）
5. **mypy strict + ruff + pytest** — 262 个测试，类型完全覆盖
6. **纯函数分析** — 所有 analysis 模块无 IO、无副作用，frozen-slots dataclass 输出
