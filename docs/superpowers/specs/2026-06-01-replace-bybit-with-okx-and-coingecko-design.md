# 替换 Bybit：OKX + CoinGecko 方案

## 背景

Bybit API 存在 IP 限制（403/CloudFront 封锁），需要替换掉所有直接调用 Bybit API 的地方。

## 目标

- K线数据改用 OKX 作为主数据源
- 持仓量(OI)和资金费率中涉及 Bybit 的数据改用 CoinGecko derivatives API 获取
- 完全移除对 BybitRestClient 的运行时依赖（保留文件供未来使用）

## 变更范围

| 组件 | 变更 |
|------|------|
| `pa_assistant/ingestion/okx.py` | 新增 `get_klines()` 和 `okx_klines_to_polars()` |
| `pa_assistant/ingestion/coingecko.py` | 新增 `get_bybit_futures_ticker()` + `parse_coingecko_bybit_ticker()` |
| `pa_assistant/scheduler.py` | K线改用 OKX 主源；OI 移除 Bybit 备用层 |
| `pa_assistant/ingestion/funding.py` | `_fetch_bybit` 改用 CoinGecko；移除构造函数中 BybitRestClient 参数 |
| `tests/` | 新增 OKX klines 和 CoinGecko Bybit ticker 测试 |

## 详细设计

### 1. OKX K线接口 (`okx.py`)

新增方法：

```python
async def get_klines(
    self,
    inst_id: str,
    bar: str = "1m",
    *,
    after: int | None = None,
    before: int | None = None,
    limit: int = 300,
) -> list[dict[str, Any]]:
```

- 端点: `GET /api/v5/market/candles`
- 参数映射: `inst_id` = `"BTC-USDT-SWAP"`, `bar` = `"1m"` / `"5m"` / `"1H"` / `"4H"` / `"1D"`
- 返回: OKX 原始 candle 列表

新增转换函数 `okx_klines_to_polars()`，输出与 `kline_1m` 表 schema 一致的 Polars DataFrame。

OKX K线格式: `[ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]`

### 2. CoinGecko Bybit ticker (`coingecko.py`)

新增方法：

```python
async def get_bybit_futures_ticker(
    self, symbol: str = "BTCUSDT"
) -> dict[str, Any] | None:
```

- 复用 `get_derivatives_tickers()` 返回的数据
- 过滤条件: `market` 包含 "Bybit"，`symbol` 匹配
- 返回: 原始 ticker dict 或 `None`

新增解析函数 `parse_coingecko_bybit_ticker()`，逻辑与 `parse_coingecko_binance_ticker` 相同（OI USD → base asset 转换）。

### 3. Scheduler 变更 (`scheduler.py`)

**K线拉取（fetch_latest_data 第1步）：**
```
当前: Bybit 主 → Binance 备
改为: OKX 主 → Binance 备
```

- OKX symbol 格式: `BTC-USDT-SWAP`（从 `BTCUSDT` 转换）
- OKX klines 限制: 单次最多 300 条，需要分页循环拉取
- 保留 Binance 作为备用

**OI 拉取（fetch_latest_data 第2步）：**
```
当前: CoinGecko → Bybit → Binance
改为: CoinGecko → Binance
```

- 移除 Bybit 备用层（第136-146行）

### 4. Funding 聚合变更 (`funding.py`)

**`_fetch_bybit` 方法重写：**
- 不再调用 `self.bybit.get_funding_rate()` / `self.bybit.get_open_interest()`
- 改为调用 CoinGecko `get_bybit_futures_ticker()` 获取 funding_rate 和 OI
- 需要在 `SelfAggregatedFundingProvider` 中新增 CoinGecko client 实例

**构造函数变更：**
- 移除 `bybit: BybitRestClient` 参数
- 保留 `coingecko_api_key` 和 `coingecko_base_url`（已有）
- `from_settings` 不再创建 BybitRestClient

**`aclose` 变更：**
- 移除 `self.bybit.aclose()`

**`_fetch_binance` 变更：**
- 移除第263-276行的 Bybit 备用逻辑

### 5. 测试

- `tests/unit/test_okx_klines.py` — OKX klines 拉取和 Polars 转换
- `tests/unit/test_coingecko.py` — 新增 Bybit ticker 解析测试
- 更新现有 mock 以适配新的调用链

## 降级策略

| 场景 | 降级行为 |
|------|---------|
| OKX K线失败 | 自动回退到 Binance |
| CoinGecko Bybit ticker 失败 | 跳过 Bybit，用剩余 4 源加权 |
| CoinGecko 整体不可用 | Binance OI 直连兜底 |
