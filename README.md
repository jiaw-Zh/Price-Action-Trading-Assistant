# Price Action Trading Assistant

一个面向比特币合约交易者的**价格行为辅助决策系统**。

不抓新闻、不堆指标、不自动下单。
只做一件事：**把市场结构、流动性、量价关系，翻译成人能读懂的「市场情境报告」。**

---

## 核心理念

- **价格是唯一的真相** — 一切信息最终都反映在 K 线上
- **市场由流动性驱动** — 合约本质是猎杀止损的游戏
- **辅助决策，不替代决策** — 扣扳机的永远是人
- **可解释性优先** — 每一个标注都能追溯到 K 线和逻辑

## 交易理论基石

Wyckoff · Smart Money Concepts · ICT · VSA · 量价背离 · 流动性猎杀

## 核心能力（规划中）

- 📊 多周期市场结构识别（BOS / CHoCH）
- 💧 流动性引擎（Order Block / FVG / Stop Hunt / 爆仓热力图）
- 📈 量价分析（CVD / Delta / 量价背离）
- 🎯 Wyckoff 阶段自动标注
- 🔔 基于情境组合的智能告警
- 📝 交易日志 + 情境快照复盘

## 文档

- [系统架构与设计理念](./docs/ARCHITECTURE.md)

## 项目状态

✅ **Phase 0 — 基础设施 + 数据接入**
- 项目骨架、`pyproject.toml`、ruff/mypy/pytest 工具链
- 配置管理（`pydantic-settings`）
- 结构化日志（`structlog`）
- DuckDB schema + repository
- **Binance Futures REST 客户端**（async + 指数退避重试）
- **多所自聚合资金费率**（Binance + OKX + Bybit，按 OI 加权）
- **`FundingProvider` 抽象**（Coinglass 接入留 stub，未来切换零代码改动）
- 批量 upsert 写入器（Polars → DuckDB Arrow 桥接）
- CLI：`init-db` / `show-config` / `backfill` / `poll-oi` / `poll-funding`

🚧 待办（Phase 0 剩余 + Phase 1 起步）
- WebSocket：K 线 / aggTrade / forceOrder（爆仓流）
- 数据完整性校验（漏单检测）
- **Phase 1 — 分析引擎**（结构识别、流动性、量价分析）

## 快速开始

```bash
# 安装依赖（首次运行会自动建立 .venv）
uv sync --extra dev

# 初始化 DuckDB 表结构
uv run pa init-db

# 回填 7 天历史 K 线（默认 BTCUSDT 1m）
uv run pa backfill --days 7

# 拉取一次当前 OI 快照
uv run pa poll-oi

# 拉取一次 OI 加权资金费率（自聚合 Binance + OKX + Bybit）
uv run pa poll-funding

# 查看当前配置（密钥自动脱敏）
uv run pa show-config

# 全套质量检查
make check        # = lint + typecheck + test
```

> ⚠️ **IP 区域限制**：`fapi.binance.com` 在某些 IP 段返回 451，`api.bybit.com` 在某些
> IP 段返回 403。两类问题对系统的影响：
>
> - Binance 主网不可达 → 设置 `BINANCE_REST_BASE_URL=https://testnet.binancefuture.com`
>   切换到测试网做开发验证
> - Bybit 不可达 → 自聚合资金费率会自动跳过 Bybit，从 Binance + OKX 计算（架构设计如此）

环境变量请参考 [`.env.example`](./.env.example)。

## 目录结构

```
pa_assistant/
├── config.py                  # pydantic-settings
├── logging.py                 # structlog 封装
├── cli.py                     # typer 命令行
├── ingestion/
│   ├── _http.py               # 共享 async HTTP 基类（重试 + 生命周期）
│   ├── binance.py             # Binance Futures REST
│   ├── okx.py                 # OKX V5 public REST
│   ├── bybit.py               # Bybit V5 public REST
│   └── funding.py             # FundingProvider 抽象 + 自聚合 + Coinglass stub
└── storage/
    ├── schema.py              # DuckDB DDL
    ├── repository.py          # Database 连接管理
    └── writers.py             # 批量 upsert（idempotent）
tests/                         # pytest 单测（73 个）
docs/                          # 设计文档
```
