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

✅ **Phase 0 — 基础设施**（部分完成）
- 项目骨架、`pyproject.toml`、ruff/mypy/pytest 工具链
- 配置管理（`pydantic-settings`）
- 结构化日志（`structlog`）
- DuckDB schema + repository
- CLI 入口（`pa version` / `pa init-db` / `pa show-config`）

🚧 待办（Phase 0 剩余）
- Binance WebSocket 接入：K 线 / Trades / 爆仓流
- Binance REST：历史 K 线补齐 + OI 轮询
- Coinglass REST + 自聚合降级
- 数据完整性校验

## 快速开始

```bash
# 安装依赖（首次运行会自动建立 .venv）
uv sync --extra dev

# 初始化 DuckDB 表结构
uv run pa init-db

# 查看当前配置（密钥自动脱敏）
uv run pa show-config

# 运行测试 / 类型检查 / 代码风格
make check        # = lint + typecheck + test
```

环境变量请参考 [`.env.example`](./.env.example)。

## 目录结构

```
pa_assistant/        # 主包
├── config.py        # pydantic-settings
├── logging.py       # structlog 封装
├── cli.py           # typer 命令行
└── storage/
    ├── schema.py    # DuckDB DDL
    └── repository.py
tests/               # pytest 单测 + 集成测占位
docs/                # 设计文档
```
