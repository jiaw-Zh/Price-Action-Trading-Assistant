# Web 端实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 PA Trading Assistant 构建 Web 界面，支持交互式分析和历史回放

**Architecture:** FastAPI 后端 + HTMX 前端交互 + Lightweight Charts K 线图 + Jinja2 模板渲染

**Tech Stack:** FastAPI, HTMX, Jinja2, Lightweight Charts, Tailwind CSS (CDN), DuckDB

---

## 文件结构

```
pa_assistant/web/
├── __init__.py              # Web 模块初始化
├── app.py                   # FastAPI 应用入口
├── routes/
│   ├── __init__.py
│   ├── pages.py             # 页面路由 (/, /liquidity, /backtest)
│   ├── api.py               # API 路由 (/api/analyze, /api/klines)
│   └── ws.py                # WebSocket 路由 (/ws/replay)
├── templates/
│   ├── base.html            # 基础模板 (导航/布局/样式)
│   ├── dashboard.html       # 主仪表盘
│   ├── liquidity.html       # 流动性分析页
│   └── backtest.html        # 回测回放页
├── static/
│   ├── css/
│   │   └── custom.css       # 自定义样式
│   └── js/
│       ├── chart.js         # Lightweight Charts 初始化
│       └── replay.js        # 回放控制逻辑
└── schemas.py               # Pydantic 请求/响应模型
```

---

## Task 1: 项目依赖配置

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 Web 依赖到 pyproject.toml**

在 `[project.optional-dependencies]` 下添加 `web` 组：

```toml
web = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]
```

- [ ] **Step 2: 安装依赖**

Run: `uv sync --extra web --extra dev`
Expected: 安装成功，无错误

- [ ] **Step 3: 验证 FastAPI 可导入**

Run: `uv run python -c "import fastapi; print(fastapi.__version__)"`
Expected: 输出版本号

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: 添加 Web 端依赖 (FastAPI + HTMX + Jinja2)"
```

---

## Task 2: Web 模块骨架

**Files:**
- Create: `pa_assistant/web/__init__.py`
- Create: `pa_assistant/web/app.py`
- Create: `pa_assistant/web/routes/__init__.py`
- Create: `pa_assistant/web/routes/pages.py`
- Create: `pa_assistant/web/schemas.py`

- [ ] **Step 1: 创建 web 模块 __init__.py**

```python
# pa_assistant/web/__init__.py
"""Web interface for PA Trading Assistant."""
```

- [ ] **Step 2: 创建 FastAPI 应用入口**

```python
# pa_assistant/web/app.py
"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pa_assistant.web.routes import pages

BASE_DIR = Path(__file__).parent

app = FastAPI(title="PA Trading Assistant", version="0.1.0")

# Mount static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Templates
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Include routes
app.include_router(pages.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
```

- [ ] **Step 3: 创建路由模块**

```python
# pa_assistant/web/routes/__init__.py
"""Web routes."""
```

```python
# pa_assistant/web/routes/pages.py
"""Page routes for server-rendered HTML."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Main dashboard page."""
    from pa_assistant.web.app import templates

    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.get("/liquidity", response_class=HTMLResponse)
async def liquidity(request: Request) -> HTMLResponse:
    """Liquidity analysis page."""
    from pa_assistant.web.app import templates

    return templates.TemplateResponse("liquidity.html", {"request": request})


@router.get("/backtest", response_class=HTMLResponse)
async def backtest(request: Request) -> HTMLResponse:
    """Backtest replay page."""
    from pa_assistant.web.app import templates

    return templates.TemplateResponse("backtest.html", {"request": request})
```

- [ ] **Step 4: 创建请求/响应模型**

```python
# pa_assistant/web/schemas.py
"""Pydantic models for API requests and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Analysis API request."""

    symbol: str = "BTCUSDT"
    timeframe: str = "1h"
    htf: str | None = None
    swing_lookback: int = Field(default=3, ge=1, le=5)
    eq_tolerance_bps: float = Field(default=10.0, ge=0.1)
    volume_climax_z: float = Field(default=2.0, ge=0.5)
    modules: list[str] = Field(
        default_factory=lambda: [
            "structure",
            "zones",
            "liquidity",
            "wyckoff",
            "divergence",
        ]
    )


class OHLCVBar(BaseModel):
    """Single OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class WyckoffState(BaseModel):
    """Wyckoff analysis state."""

    phase: str
    confidence: float
    range_low: float | None = None
    range_high: float | None = None
    next_watch: str = ""


class TrendState(BaseModel):
    """Trend analysis state."""

    working: Literal["up", "down", "none"]
    htf: Literal["up", "down", "none"] = "none"
    alignment: str = ""


class LiquidityLevel(BaseModel):
    """Liquidity pool."""

    price: float
    side: Literal["high", "low"]
    touches: int
    spread_bps: float
    distance: float
    distance_pct: float
    status: Literal["active", "swept"]


class StructureEvent(BaseModel):
    """Structure event (BOS/CHoCH)."""

    timestamp: datetime
    event_type: str
    level: float
    trend_before: str
    trend_after: str


class OrderBlock(BaseModel):
    """Order block."""

    timestamp: datetime
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    status: Literal["active", "mitigated"]


class FairValueGap(BaseModel):
    """Fair value gap."""

    timestamp: datetime
    direction: Literal["bullish", "bearish"]
    top: float
    bottom: float
    status: Literal["unfilled", "filled"]


class Divergence(BaseModel):
    """Divergence event."""

    timestamp: datetime
    indicator: str
    side: Literal["bullish", "bearish"]
    strength: float
    swing_price: float
    indicator_value: float


class Scorecard(BaseModel):
    """Analysis scorecard."""

    net_bias: Literal["bullish", "bearish", "neutral"]
    bullish_factors: list[str] = Field(default_factory=list)
    bearish_factors: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    """Analysis API response."""

    timestamp: datetime
    symbol: str
    timeframe: str
    current_price: float

    wyckoff: WyckoffState | None = None
    trend: TrendState | None = None
    liquidity_levels: list[LiquidityLevel] = Field(default_factory=list)
    order_blocks: list[OrderBlock] = Field(default_factory=list)
    fvgs: list[FairValueGap] = Field(default_factory=list)
    structure_events: list[StructureEvent] = Field(default_factory=list)
    divergences: list[Divergence] = Field(default_factory=list)
    scorecard: Scorecard | None = None


class KlineResponse(BaseModel):
    """K-line data response."""

    bars: list[OHLCVBar]
    total: int
```

- [ ] **Step 5: 验证模块可导入**

Run: `uv run python -c "from pa_assistant.web.app import app; print(app.title)"`
Expected: 输出 "PA Trading Assistant"

- [ ] **Step 6: Commit**

```bash
git add pa_assistant/web/
git commit -m "feat(web): 创建 FastAPI 应用骨架 + 路由 + schemas"
```

---

## Task 3: 基础模板 + 深色主题

**Files:**
- Create: `pa_assistant/web/templates/base.html`
- Create: `pa_assistant/web/static/css/custom.css`
- Create: `pa_assistant/web/static/js/chart.js`

- [ ] **Step 1: 创建目录结构**

Run:
```bash
mkdir -p pa_assistant/web/templates
mkdir -p pa_assistant/web/static/css
mkdir -p pa_assistant/web/static/js
```

- [ ] **Step 2: 创建 base.html 基础模板**

```html
<!-- pa_assistant/web/templates/base.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}PA Assistant{% endblock %}</title>

    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        primary: '#F59E0B',
                        secondary: '#FBBF24',
                        cta: '#8B5CF6',
                        dark: {
                            900: '#0F172A',
                            800: '#1E293B',
                            700: '#334155',
                            600: '#475569',
                            400: '#94A3B8',
                            50: '#F8FAFC',
                        }
                    }
                }
            }
        }
    </script>

    <!-- HTMX -->
    <script src="https://unpkg.com/htmx.org@1.9.12"></script>

    <!-- Google Fonts: Orbitron + Exo 2 -->
    <link href="https://fonts.googleapis.com/css2?family=Exo+2:wght@300;400;500;600;700&family=Orbitron:wght@400;500;600;700&display=swap" rel="stylesheet">

    <!-- Lightweight Charts -->
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>

    <!-- Custom CSS -->
    <link rel="stylesheet" href="/static/css/custom.css">

    <style>
        body {
            font-family: 'Exo 2', sans-serif;
            background-color: #0F172A;
            color: #F8FAFC;
        }
        h1, h2, h3, .font-display {
            font-family: 'Orbitron', sans-serif;
        }
    </style>

    {% block head %}{% endblock %}
</head>
<body class="dark">
    <!-- Navigation -->
    <nav class="bg-dark-800 border-b border-dark-700 px-4 py-3">
        <div class="max-w-7xl mx-auto flex justify-between items-center">
            <div class="flex items-center gap-4">
                <a href="/" class="text-primary font-bold text-xl font-display">PA Assistant</a>
                <span id="symbol-badge" class="text-dark-400 text-sm">BTCUSDT</span>
                <span id="price-badge" class="bg-dark-900 text-green-500 px-2 py-1 rounded text-sm font-mono">$0.00</span>
            </div>
            <div class="flex gap-6 text-sm">
                <a href="/" class="text-dark-400 hover:text-primary transition-colors {% if request.url.path == '/' %}text-primary font-bold{% endif %}">仪表盘</a>
                <a href="/liquidity" class="text-dark-400 hover:text-primary transition-colors {% if request.url.path == '/liquidity' %}text-primary font-bold{% endif %}">流动性</a>
                <a href="/backtest" class="text-dark-400 hover:text-primary transition-colors {% if request.url.path == '/backtest' %}text-primary font-bold{% endif %}">回测</a>
            </div>
        </div>
    </nav>

    <!-- Main Content -->
    <main class="max-w-7xl mx-auto">
        {% block content %}{% endblock %}
    </main>

    <!-- Scripts -->
    {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 3: 创建 custom.css**

```css
/* pa_assistant/web/static/css/custom.css */

/* Scrollbar styling */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}

::-webkit-scrollbar-track {
    background: #1E293B;
}

::-webkit-scrollbar-thumb {
    background: #475569;
    border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
    background: #64748B;
}

/* Slider styling */
input[type="range"] {
    -webkit-appearance: none;
    appearance: none;
    height: 4px;
    background: #334155;
    border-radius: 2px;
    outline: none;
}

input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 16px;
    height: 16px;
    background: #F59E0B;
    border-radius: 50%;
    cursor: pointer;
}

/* Card hover effect */
.card-hover {
    transition: all 0.2s ease;
}

.card-hover:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(245, 158, 11, 0.1);
}

/* Loading animation */
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.animate-pulse {
    animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
```

- [ ] **Step 4: 创建 chart.js 基础封装**

```javascript
// pa_assistant/web/static/js/chart.js

/**
 * Lightweight Charts wrapper for PA Assistant
 */
class PAChart {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) {
            console.error(`Container #${containerId} not found`);
            return;
        }

        this.chart = LightweightCharts.createChart(this.container, {
            layout: {
                background: { color: '#1E293B' },
                textColor: '#94A3B8',
            },
            grid: {
                vertLines: { color: '#334155' },
                horzLines: { color: '#334155' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: '#334155',
            },
            timeScale: {
                borderColor: '#334155',
                timeVisible: true,
                secondsVisible: false,
            },
            ...options,
        });

        // Main candlestick series
        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#22C55E',
            downColor: '#EF4444',
            borderUpColor: '#22C55E',
            borderDownColor: '#EF4444',
            wickUpColor: '#22C55E',
            wickDownColor: '#EF4444',
        });

        // Volume series
        this.volumeSeries = this.chart.addHistogramSeries({
            color: '#64748B',
            priceFormat: { type: 'volume' },
            priceScaleId: '',
        });

        this.markers = [];
        this.lines = [];
        this.rectangles = [];

        // Handle resize
        this.resizeObserver = new ResizeObserver(() => {
            this.chart.applyOptions({
                width: this.container.clientWidth,
                height: this.container.clientHeight,
            });
        });
        this.resizeObserver.observe(this.container);
    }

    /**
     * Set OHLCV data
     */
    setData(bars) {
        const candleData = bars.map(b => ({
            time: b.timestamp,
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
        }));

        const volumeData = bars.map(b => ({
            time: b.timestamp,
            value: b.volume,
            color: b.close >= b.open ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)',
        }));

        this.candleSeries.setData(candleData);
        this.volumeSeries.setData(volumeData);
    }

    /**
     * Add a single bar (for replay)
     */
    addBar(bar) {
        this.candleSeries.update({
            time: bar.timestamp,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
        });
        this.volumeSeries.update({
            time: bar.timestamp,
            value: bar.volume,
            color: bar.close >= bar.open ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)',
        });
    }

    /**
     * Add price line (liquidity levels, etc.)
     */
    addPriceLine(options) {
        const line = this.candleSeries.createPriceLine({
            price: options.price,
            color: options.color || '#F59E0B',
            lineWidth: options.lineWidth || 1,
            lineStyle: options.lineStyle || LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: options.title || '',
        });
        this.lines.push(line);
        return line;
    }

    /**
     * Remove all price lines
     */
    clearLines() {
        this.lines.forEach(line => this.candleSeries.removePriceLine(line));
        this.lines = [];
    }

    /**
     * Set markers (events)
     */
    setMarkers(markers) {
        this.candleSeries.setMarkers(markers.map(m => ({
            time: m.timestamp,
            position: m.side === 'bullish' ? 'belowBar' : 'aboveBar',
            color: m.side === 'bullish' ? '#22C55E' : '#EF4444',
            shape: m.side === 'bullish' ? 'arrowUp' : 'arrowDown',
            text: m.text,
        })));
    }

    /**
     * Fit content to view
     */
    fitContent() {
        this.chart.timeScale().fitContent();
    }

    /**
     * Scroll to time
     */
    scrollToTime(timestamp) {
        this.chart.timeScale().scrollToPosition(
            this.chart.timeScale().coordinateToLogical(this.container.clientWidth) || 0,
            false
        );
    }

    /**
     * Destroy chart
     */
    destroy() {
        this.resizeObserver.disconnect();
        this.chart.remove();
    }
}

// Export for use in other scripts
window.PAChart = PAChart;
```

- [ ] **Step 5: Commit**

```bash
git add pa_assistant/web/templates/ pa_assistant/web/static/
git commit -m "feat(web): 创建基础模板 + 深色主题 + 图表封装"
```

---

## Task 4: 主仪表盘页面

**Files:**
- Create: `pa_assistant/web/templates/dashboard.html`

- [ ] **Step 1: 创建 dashboard.html**

```html
<!-- pa_assistant/web/templates/dashboard.html -->
{% extends "base.html" %}

{% block title %}PA Assistant - 仪表盘{% endblock %}

{% block content %}
<div class="flex h-[calc(100vh-64px)]">
    <!-- 左侧参数面板 -->
    <aside class="w-60 bg-dark-900 border-r border-dark-700 p-4 overflow-y-auto flex-shrink-0">
        <h2 class="text-primary font-bold mb-4 font-display">参数设置</h2>

        <!-- 时间周期 -->
        <div class="mb-5">
            <label class="text-dark-400 text-xs mb-2 block">时间周期</label>
            <div class="flex gap-1 flex-wrap" id="timeframe-selector">
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400 hover:bg-dark-700 transition-colors" data-tf="5m">5m</button>
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400 hover:bg-dark-700 transition-colors" data-tf="15m">15m</button>
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-primary text-dark-900 font-bold" data-tf="1h">1h</button>
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400 hover:bg-dark-700 transition-colors" data-tf="4h">4h</button>
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400 hover:bg-dark-700 transition-colors" data-tf="1d">1d</button>
            </div>
            <input type="hidden" id="timeframe" value="1h">
        </div>

        <!-- HTF 周期 -->
        <div class="mb-5">
            <label class="text-dark-400 text-xs mb-2 block">HTF 周期 (可选)</label>
            <div class="flex gap-1" id="htf-selector">
                <button class="htf-btn px-2 py-1 rounded text-xs cursor-pointer bg-primary text-dark-900 font-bold" data-htf="">无</button>
                <button class="htf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400 hover:bg-dark-700 transition-colors" data-htf="4h">4h</button>
                <button class="htf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400 hover:bg-dark-700 transition-colors" data-htf="1d">1d</button>
            </div>
            <input type="hidden" id="htf" value="">
        </div>

        <!-- Swing Lookback -->
        <div class="mb-5">
            <label class="text-dark-400 text-xs mb-2 block">Swing Lookback: <span id="lookback-value">3</span></label>
            <input type="range" id="swing-lookback" min="1" max="5" value="3" class="w-full">
        </div>

        <!-- 容差 -->
        <div class="mb-5">
            <label class="text-dark-400 text-xs mb-2 block">流动性池容差: <span id="tolerance-value">10</span> bps</label>
            <input type="range" id="tolerance" min="1" max="20" value="10" class="w-full">
        </div>

        <!-- Volume Climax Z -->
        <div class="mb-5">
            <label class="text-dark-400 text-xs mb-2 block">Volume Climax Z: <span id="climax-z-value">2.0</span></label>
            <input type="range" id="climax-z" min="1" max="4" value="2" step="0.5" class="w-full">
        </div>

        <!-- 分析模块开关 -->
        <div class="mb-5">
            <label class="text-dark-400 text-xs mb-2 block">显示模块</label>
            <div class="flex flex-col gap-2">
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" id="show-structure" checked class="accent-primary"> 结构事件
                </label>
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" id="show-obs" checked class="accent-primary"> 订单块
                </label>
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" id="show-fvgs" checked class="accent-primary"> FVG
                </label>
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" id="show-liquidity" checked class="accent-primary"> 流动性池
                </label>
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" id="show-vwap" class="accent-primary"> VWAP
                </label>
            </div>
        </div>

        <!-- 运行按钮 -->
        <button
            id="run-analysis"
            class="w-full bg-primary text-dark-900 py-2.5 rounded-lg font-bold cursor-pointer hover:bg-secondary transition-colors"
            hx-post="/api/analyze"
            hx-include="#timeframe, #htf, #swing-lookback, #tolerance, #climax-z"
            hx-target="#analysis-results"
            hx-swap="innerHTML"
        >
            运行分析
        </button>
    </aside>

    <!-- 中间 K 线图 -->
    <div class="flex-1 bg-dark-900 p-4 flex flex-col">
        <!-- 图表工具栏 -->
        <div class="flex justify-between mb-3">
            <div class="flex gap-2">
                <button class="bg-dark-800 text-dark-400 px-2 py-1 rounded text-xs cursor-pointer hover:bg-dark-700">十字线</button>
                <button class="bg-dark-800 text-dark-400 px-2 py-1 rounded text-xs cursor-pointer hover:bg-dark-700">测量</button>
                <button class="bg-dark-800 text-dark-400 px-2 py-1 rounded text-xs cursor-pointer hover:bg-dark-700" onclick="takeScreenshot()">截图</button>
            </div>
            <div class="flex gap-2">
                <button class="bg-dark-800 text-dark-400 px-2 py-1 rounded text-xs cursor-pointer hover:bg-dark-700" onclick="chart.fitContent()">⟳</button>
            </div>
        </div>

        <!-- 图表容器 -->
        <div id="chart-container" class="flex-1 bg-dark-800 rounded-lg relative">
            <!-- Chart will be rendered here by Lightweight Charts -->
        </div>

        <!-- 图例 -->
        <div class="flex gap-4 mt-2 text-xs text-dark-400">
            <span><span class="text-primary">■</span> OB</span>
            <span><span class="text-purple-500">■</span> FVG</span>
            <span><span class="text-green-500">---</span> 流动性</span>
            <span><span class="text-blue-500">---</span> BOS/CHoCH</span>
        </div>
    </div>

    <!-- 右侧分析结果 -->
    <aside class="w-72 bg-dark-900 border-l border-dark-700 p-4 overflow-y-auto flex-shrink-0">
        <h2 class="text-primary font-bold mb-4 font-display">分析结果</h2>

        <div id="analysis-results">
            <!-- 默认状态 -->
            <div class="text-dark-400 text-sm text-center py-8">
                点击「运行分析」查看结果
            </div>
        </div>
    </aside>
</div>
{% endblock %}

{% block scripts %}
<script src="/static/js/chart.js"></script>
<script>
    // Initialize chart
    const chart = new PAChart('chart-container');

    // Timeframe selector
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tf-btn').forEach(b => {
                b.classList.remove('bg-primary', 'text-dark-900', 'font-bold');
                b.classList.add('bg-dark-800', 'text-dark-400');
            });
            btn.classList.remove('bg-dark-800', 'text-dark-400');
            btn.classList.add('bg-primary', 'text-dark-900', 'font-bold');
            document.getElementById('timeframe').value = btn.dataset.tf;
        });
    });

    // HTF selector
    document.querySelectorAll('.htf-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.htf-btn').forEach(b => {
                b.classList.remove('bg-primary', 'text-dark-900', 'font-bold');
                b.classList.add('bg-dark-800', 'text-dark-400');
            });
            btn.classList.remove('bg-dark-800', 'text-dark-400');
            btn.classList.add('bg-primary', 'text-dark-900', 'font-bold');
            document.getElementById('htf').value = btn.dataset.htf;
        });
    });

    // Slider value display
    document.getElementById('swing-lookback').addEventListener('input', (e) => {
        document.getElementById('lookback-value').textContent = e.target.value;
    });
    document.getElementById('tolerance').addEventListener('input', (e) => {
        document.getElementById('tolerance-value').textContent = e.target.value;
    });
    document.getElementById('climax-z').addEventListener('input', (e) => {
        document.getElementById('climax-z-value').textContent = parseFloat(e.target.value).toFixed(1);
    });

    // Load initial data
    async function loadKlines() {
        const tf = document.getElementById('timeframe').value;
        const resp = await fetch(`/api/klines?timeframe=${tf}&limit=500`);
        const data = await resp.json();
        chart.setData(data.bars);
        chart.fitContent();
    }

    // Load on page ready
    document.addEventListener('DOMContentLoaded', loadKlines);

    // Reload when timeframe changes
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', () => setTimeout(loadKlines, 100));
    });

    // Screenshot function
    function takeScreenshot() {
        const canvas = document.querySelector('#chart-container canvas');
        if (canvas) {
            const link = document.createElement('a');
            link.download = 'chart.png';
            link.href = canvas.toDataURL();
            link.click();
        }
    }
</script>
{% endblock %}
```

- [ ] **Step 2: 验证页面可访问**

Run: `uv run uvicorn pa_assistant.web.app:app --port 8765 &`
然后访问 http://localhost:8765/
Expected: 看到深色主题的仪表盘页面

- [ ] **Step 3: Commit**

```bash
git add pa_assistant/web/templates/dashboard.html
git commit -m "feat(web): 创建主仪表盘页面 (三栏布局)"
```

---

## Task 5: 分析 API 实现

**Files:**
- Create: `pa_assistant/web/routes/api.py`
- Modify: `pa_assistant/web/app.py`

- [ ] **Step 1: 创建 api.py**

```python
# pa_assistant/web/routes/api.py
"""API routes for data and analysis."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb
from fastapi import APIRouter, Query

from pa_assistant.analysis import (
    analyze_wyckoff,
    compute_delta,
    detect_divergences,
    detect_fvgs,
    detect_liquidity_levels,
    detect_order_blocks,
    detect_stop_hunts,
    detect_structure_events,
    detect_swings,
    resample_ohlcv,
)
from pa_assistant.config import get_settings
from pa_assistant.web.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    Divergence,
    FairValueGap,
    KlineResponse,
    LiquidityLevel,
    OHLCVBar,
    OrderBlock,
    Scorecard,
    StructureEvent,
    TrendState,
    WyckoffState,
)

router = APIRouter(prefix="/api")


@router.get("/klines", response_model=KlineResponse)
async def get_klines(
    symbol: str = Query(default="BTCUSDT"),
    timeframe: str = Query(default="1h"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> KlineResponse:
    """Get OHLCV kline data."""
    settings = get_settings()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [symbol],
        ).pl()
    finally:
        conn.close()

    if df.is_empty():
        return KlineResponse(bars=[], total=0)

    # Resample to target timeframe
    resampled = resample_ohlcv(df, timeframe)

    # Take last N bars
    if resampled.height > limit:
        resampled = resampled.tail(limit)

    bars = []
    for row in resampled.iter_rows(named=True):
        bars.append(
            OHLCVBar(
                timestamp=row["open_time"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )

    return KlineResponse(bars=bars, total=len(bars))


@router.post("/analyze", response_model=AnalyzeResponse)
async def run_analysis(request: AnalyzeRequest) -> AnalyzeResponse:
    """Run full analysis on stored klines."""
    settings = get_settings()

    # Load data from DuckDB
    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [request.symbol],
        ).pl()
        oi_df = conn.execute(
            "SELECT timestamp AS open_time, open_interest AS oi "
            "FROM oi_1m WHERE symbol = ? ORDER BY timestamp",
            [request.symbol],
        ).pl()
    finally:
        conn.close()

    if klines.is_empty():
        return AnalyzeResponse(
            timestamp=datetime.now(UTC),
            symbol=request.symbol,
            timeframe=request.timeframe,
            current_price=0.0,
        )

    # Resample
    working = resample_ohlcv(klines, request.timeframe)
    working = compute_delta(working)

    if not oi_df.is_empty():
        working = working.sort("open_time").join_asof(
            oi_df.sort("open_time"), on="open_time", strategy="backward"
        )

    last_row = working.row(working.height - 1, named=True)
    last_close = float(last_row["close"])
    last_ts = last_row["open_time"]

    # Run detectors
    annotated = detect_swings(working, lookback=request.swing_lookback)
    structure_events = detect_structure_events(annotated)
    liquidity_levels = detect_liquidity_levels(
        working, tolerance_bps=request.eq_tolerance_bps
    )
    order_blocks = detect_order_blocks(working, structure_events)
    fvgs = detect_fvgs(working)
    divergences = detect_divergences(working)

    # Wyckoff
    wyckoff_snaps = analyze_wyckoff(
        working,
        swing_lookback=request.swing_lookback,
        volume_climax_z=request.volume_climax_z,
        eq_tolerance_bps=request.eq_tolerance_bps,
        divergences=divergences,
    )
    wyckoff_snap = wyckoff_snaps[-1]

    # Trend
    working_trend = "none"
    if structure_events:
        last_ev = structure_events[-1]
        if last_ev.event_type in {"BOS_up", "CHoCH_up"}:
            working_trend = "up"
        elif last_ev.event_type in {"BOS_down", "CHoCH_down"}:
            working_trend = "down"

    htf_trend = "none"
    if request.htf:
        htf_df = resample_ohlcv(klines, request.htf)
        htf_annotated = detect_swings(htf_df, lookback=request.swing_lookback)
        htf_events = detect_structure_events(htf_annotated)
        if htf_events:
            last_ev = htf_events[-1]
            if last_ev.event_type in {"BOS_up", "CHoCH_up"}:
                htf_trend = "up"
            elif last_ev.event_type in {"BOS_down", "CHoCH_down"}:
                htf_trend = "down"

    alignment = "无"
    if working_trend == "up" and htf_trend == "up":
        alignment = "双周期一致看多"
    elif working_trend == "down" and htf_trend == "down":
        alignment = "双周期一致看空"

    # Build response
    return AnalyzeResponse(
        timestamp=last_ts,
        symbol=request.symbol,
        timeframe=request.timeframe,
        current_price=last_close,
        wyckoff=WyckoffState(
            phase=wyckoff_snap.phase.value,
            confidence=wyckoff_snap.confidence,
            range_low=wyckoff_snap.range_low,
            range_high=wyckoff_snap.range_high,
            next_watch=wyckoff_snap.next_watch or "",
        ),
        trend=TrendState(
            working=working_trend,  # type: ignore[arg-type]
            htf=htf_trend,  # type: ignore[arg-type]
            alignment=alignment,
        ),
        liquidity_levels=[
            LiquidityLevel(
                price=lv.price,
                side=lv.side,  # type: ignore[arg-type]
                touches=len(lv.touches),
                spread_bps=lv.spread_bps,
                distance=lv.price - last_close,
                distance_pct=(lv.price - last_close) / last_close * 100,
                status="swept" if lv.swept_at else "active",
            )
            for lv in liquidity_levels
        ],
        order_blocks=[
            OrderBlock(
                timestamp=ob.timestamp,
                direction=ob.direction,  # type: ignore[arg-type]
                top=ob.top,
                bottom=ob.bottom,
                status="mitigated" if ob.mitigated_at else "active",
            )
            for ob in order_blocks
        ],
        fvgs=[
            FairValueGap(
                timestamp=fvg.timestamp,
                direction=fvg.direction,  # type: ignore[arg-type]
                top=fvg.top,
                bottom=fvg.bottom,
                status="filled" if fvg.mitigated_at else "unfilled",
            )
            for fvg in fvgs
        ],
        structure_events=[
            StructureEvent(
                timestamp=ev.timestamp,
                event_type=ev.event_type,
                level=ev.level,
                trend_before=ev.trend_before,
                trend_after=ev.trend_after,
            )
            for ev in structure_events
        ],
        divergences=[
            Divergence(
                timestamp=d.timestamp,
                indicator=d.indicator,
                side=d.side,  # type: ignore[arg-type]
                strength=d.strength,
                swing_price=d.swing_price,
                indicator_value=d.indicator_value,
            )
            for d in divergences
        ],
        scorecard=Scorecard(
            net_bias="neutral",
            bullish_factors=[],
            bearish_factors=[],
        ),
    )
```

- [ ] **Step 2: 更新 app.py 注册 API 路由**

```python
# pa_assistant/web/app.py
"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pa_assistant.web.routes import api, pages

BASE_DIR = Path(__file__).parent

app = FastAPI(title="PA Trading Assistant", version="0.1.0")

# Mount static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Templates
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Include routes
app.include_router(pages.router)
app.include_router(api.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
```

- [ ] **Step 3: 验证 API**

Run: `curl -X POST http://localhost:8765/api/analyze -H "Content-Type: application/json" -d '{"symbol":"BTCUSDT","timeframe":"1h"}'`
Expected: 返回 JSON 分析结果

- [ ] **Step 4: Commit**

```bash
git add pa_assistant/web/routes/api.py pa_assistant/web/app.py
git commit -m "feat(web): 实现分析 API (/api/analyze, /api/klines)"
```

---

## Task 6: 流动性分析页

**Files:**
- Create: `pa_assistant/web/templates/liquidity.html`

- [ ] **Step 1: 创建 liquidity.html**

```html
<!-- pa_assistant/web/templates/liquidity.html -->
{% extends "base.html" %}

{% block title %}PA Assistant - 流动性分析{% endblock %}

{% block content %}
<div class="flex h-[calc(100vh-64px)]">
    <!-- 左侧流动性池列表 -->
    <aside class="w-80 bg-dark-900 border-r border-dark-700 p-4 overflow-y-auto flex-shrink-0">
        <div class="flex justify-between items-center mb-4">
            <h2 class="text-primary font-bold font-display">流动性池</h2>
            <div class="flex gap-1">
                <button class="filter-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400" data-filter="all">全部</button>
                <button class="filter-btn px-2 py-1 rounded text-xs cursor-pointer bg-primary text-dark-900 font-bold" data-filter="active">生效中</button>
                <button class="filter-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400" data-filter="swept">已扫</button>
            </div>
        </div>

        <div id="liquidity-list" class="space-y-2">
            <!-- Will be populated by HTMX -->
            <div class="text-dark-400 text-sm text-center py-8">
                加载中...
            </div>
        </div>
    </aside>

    <!-- 右侧可视化 -->
    <div class="flex-1 bg-dark-900 p-4 flex flex-col">
        <!-- 统计卡片 -->
        <div class="grid grid-cols-4 gap-3 mb-4">
            <div class="bg-dark-800 rounded-lg p-3 text-center">
                <div class="text-xs text-dark-400">生效中</div>
                <div id="stat-active" class="text-green-500 text-2xl font-bold">-</div>
            </div>
            <div class="bg-dark-800 rounded-lg p-3 text-center">
                <div class="text-xs text-dark-400">已扫</div>
                <div id="stat-swept" class="text-dark-400 text-2xl font-bold">-</div>
            </div>
            <div class="bg-dark-800 rounded-lg p-3 text-center">
                <div class="text-xs text-dark-400">Stop Hunt</div>
                <div id="stat-hunts" class="text-primary text-2xl font-bold">-</div>
            </div>
            <div class="bg-dark-800 rounded-lg p-3 text-center">
                <div class="text-xs text-dark-400">最近磁吸</div>
                <div id="stat-magnet" class="text-dark-50 text-2xl font-bold">-</div>
            </div>
        </div>

        <!-- 流动性阶梯图 -->
        <div class="flex-1 bg-dark-800 rounded-lg p-4">
            <div class="text-primary font-bold text-sm mb-3 font-display">流动性阶梯</div>
            <svg id="ladder-chart" width="100%" height="90%" viewBox="0 0 600 400">
                <!-- Will be populated by JavaScript -->
            </svg>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
    let currentFilter = 'active';
    let liquidityData = [];

    // Load liquidity data
    async function loadLiquidity() {
        const resp = await fetch('/api/liquidity');
        const data = await resp.json();
        liquidityData = data.levels;
        renderList();
        renderStats();
        renderLadder();
    }

    // Filter buttons
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-btn').forEach(b => {
                b.classList.remove('bg-primary', 'text-dark-900', 'font-bold');
                b.classList.add('bg-dark-800', 'text-dark-400');
            });
            btn.classList.remove('bg-dark-800', 'text-dark-400');
            btn.classList.add('bg-primary', 'text-dark-900', 'font-bold');
            currentFilter = btn.dataset.filter;
            renderList();
        });
    });

    // Render list
    function renderList() {
        const container = document.getElementById('liquidity-list');
        const filtered = liquidityData.filter(lv => {
            if (currentFilter === 'all') return true;
            return lv.status === currentFilter;
        });

        if (filtered.length === 0) {
            container.innerHTML = '<div class="text-dark-400 text-sm text-center py-8">无数据</div>';
            return;
        }

        container.innerHTML = filtered.map(lv => `
            <div class="bg-dark-800 rounded-lg p-3 border-l-3 ${
                lv.status === 'swept' ? 'border-dark-600' :
                lv.side === 'high' ? 'border-green-500' : 'border-red-500'
            }">
                <div class="flex justify-between items-center">
                    <div>
                        <span class="${lv.side === 'high' ? 'text-green-500' : 'text-red-500'} font-bold">
                            ${lv.side === 'high' ? '▲ 等高' : '▼ 等低'}
                        </span>
                        <span class="text-dark-50 ml-2 font-bold">$${lv.price.toLocaleString()}</span>
                    </div>
                    <span class="text-xs px-2 py-0.5 rounded ${
                        lv.status === 'swept' ? 'bg-dark-600 text-dark-400' :
                        'bg-green-500 text-dark-900'
                    }">${lv.touches}x 触碰</span>
                </div>
                <div class="text-xs text-dark-400 mt-2">
                    <div>spread: ${lv.spread_bps.toFixed(1)} bps | 距离: ${lv.distance >= 0 ? '+' : ''}$${lv.distance.toLocaleString()} (${lv.distance_pct.toFixed(1)}%)</div>
                    <div class="${lv.status === 'swept' ? 'text-dark-600' : 'text-green-500'} mt-1">
                        状态: ${lv.status === 'swept' ? '已扫' : '生效中'}
                    </div>
                </div>
            </div>
        `).join('');
    }

    // Render stats
    function renderStats() {
        const active = liquidityData.filter(l => l.status === 'active').length;
        const swept = liquidityData.filter(l => l.status === 'swept').length;
        const magnet = liquidityData
            .filter(l => l.status === 'active')
            .sort((a, b) => Math.abs(a.distance) - Math.abs(b.distance))[0];

        document.getElementById('stat-active').textContent = active;
        document.getElementById('stat-swept').textContent = swept;
        document.getElementById('stat-hunts').textContent = '-';
        document.getElementById('stat-magnet').textContent = magnet
            ? `$${(magnet.price / 1000).toFixed(1)}k`
            : '-';
    }

    // Render ladder chart
    function renderLadder() {
        const svg = document.getElementById('ladder-chart');
        const active = liquidityData.filter(l => l.status === 'active');
        if (active.length === 0) return;

        const prices = active.map(l => l.price);
        const minPrice = Math.min(...prices) * 0.999;
        const maxPrice = Math.max(...prices) * 1.001;
        const priceRange = maxPrice - minPrice;

        const currentPrice = liquidityData[0]?.price || 0; // Approximate

        let html = '';

        // Current price line
        const currentY = 200 + ((maxPrice - currentPrice) / priceRange) * 350;
        html += `<line x1="50" y1="${currentY}" x2="550" y2="${currentY}" stroke="#F59E0B" stroke-width="2"/>`;
        html += `<text x="555" y="${currentY + 4}" fill="#F59E0B" font-size="11">当前价格</text>`;

        // Liquidity levels
        active.forEach((lv, i) => {
            const y = 200 + ((maxPrice - lv.price) / priceRange) * 350;
            const color = lv.side === 'high' ? '#22C55E' : '#EF4444';
            const width = 100 + lv.touches * 20;

            html += `<rect x="100" y="${y - 10}" width="${width}" height="20" fill="${color}" fill-opacity="0.2" stroke="${color}" stroke-width="1" rx="4"/>`;
            html += `<text x="110" y="${y + 4}" fill="${color}" font-size="11">$${lv.price.toLocaleString()} (${lv.touches}x)</text>`;
        });

        svg.innerHTML = html;
    }

    // Load on page ready
    document.addEventListener('DOMContentLoaded', loadLiquidity);
</script>
{% endblock %}
```

- [ ] **Step 2: 添加流动性 API**

在 `pa_assistant/web/routes/api.py` 中添加：

```python
@router.get("/liquidity")
async def get_liquidity(symbol: str = Query(default="BTCUSDT")) -> dict[str, Any]:
    """Get liquidity levels."""
    settings = get_settings()

    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume "
            "FROM kline_1m WHERE symbol = ? ORDER BY open_time",
            [symbol],
        ).pl()
    finally:
        conn.close()

    if klines.is_empty():
        return {"levels": [], "current_price": 0}

    resampled = resample_ohlcv(klines, "1h")
    levels = detect_liquidity_levels(resampled)
    current_price = float(resampled.row(resampled.height - 1, named=True)["close"])

    return {
        "levels": [
            {
                "price": lv.price,
                "side": lv.side,
                "touches": len(lv.touches),
                "spread_bps": lv.spread_bps,
                "distance": lv.price - current_price,
                "distance_pct": (lv.price - current_price) / current_price * 100,
                "status": "swept" if lv.swept_at else "active",
            }
            for lv in levels
        ],
        "current_price": current_price,
    }
```

- [ ] **Step 3: Commit**

```bash
git add pa_assistant/web/templates/liquidity.html pa_assistant/web/routes/api.py
git commit -m "feat(web): 创建流动性分析页 + 流动性 API"
```

---

## Task 7: 回测回放页

**Files:**
- Create: `pa_assistant/web/templates/backtest.html`
- Create: `pa_assistant/web/static/js/replay.js`
- Create: `pa_assistant/web/routes/ws.py`
- Modify: `pa_assistant/web/app.py`

- [ ] **Step 1: 创建 backtest.html**

```html
<!-- pa_assistant/web/templates/backtest.html -->
{% extends "base.html" %}

{% block title %}PA Assistant - 回测回放{% endblock %}

{% block content %}
<div class="flex h-[calc(100vh-64px)]">
    <!-- 左侧控制面板 -->
    <aside class="w-72 bg-dark-900 border-r border-dark-700 p-4 overflow-y-auto flex-shrink-0">
        <h2 class="text-primary font-bold mb-4 font-display">回放设置</h2>

        <!-- 模式切换 -->
        <div class="flex gap-2 mb-5">
            <button class="mode-btn flex-1 py-2 rounded text-sm cursor-pointer bg-primary text-dark-900 font-bold" data-mode="replay">逐根回放</button>
            <button class="mode-btn flex-1 py-2 rounded text-sm cursor-pointer bg-dark-800 text-dark-400" data-mode="snapshot">区间快照</button>
        </div>

        <!-- 起始时间 -->
        <div class="mb-4">
            <label class="text-dark-400 text-xs mb-1 block">起始时间</label>
            <input type="datetime-local" id="start-time" value="2026-05-01T00:00"
                class="w-full bg-dark-800 border border-dark-700 text-dark-50 p-2 rounded text-sm">
        </div>

        <!-- 时间周期 -->
        <div class="mb-4">
            <label class="text-dark-400 text-xs mb-1 block">时间周期</label>
            <div class="flex gap-1">
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400" data-tf="5m">5m</button>
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-primary text-dark-900 font-bold" data-tf="1h">1h</button>
                <button class="tf-btn px-2 py-1 rounded text-xs cursor-pointer bg-dark-800 text-dark-400" data-tf="4h">4h</button>
            </div>
            <input type="hidden" id="replay-tf" value="1h">
        </div>

        <!-- 回放速度 -->
        <div class="mb-4">
            <label class="text-dark-400 text-xs mb-1 block">回放速度: <span id="speed-value">3x</span></label>
            <input type="range" id="replay-speed" min="1" max="10" value="3" class="w-full">
        </div>

        <!-- 回放控制 -->
        <div class="flex gap-2 mb-4">
            <button id="btn-prev" class="flex-1 bg-dark-800 text-dark-400 py-2 rounded cursor-pointer hover:bg-dark-700">⏮</button>
            <button id="btn-play" class="flex-1 bg-primary text-dark-900 py-2 rounded cursor-pointer font-bold hover:bg-secondary">▶ 播放</button>
            <button id="btn-next" class="flex-1 bg-dark-800 text-dark-400 py-2 rounded cursor-pointer hover:bg-dark-700">⏭</button>
        </div>

        <!-- 进度条 -->
        <div class="mb-4">
            <div class="flex justify-between text-xs text-dark-400 mb-1">
                <span id="progress-start">-</span>
                <span id="progress-end">-</span>
            </div>
            <input type="range" id="progress-bar" min="0" max="100" value="0" class="w-full">
            <div class="text-center text-xs text-dark-400 mt-1">
                进度: <span id="progress-pct">0%</span>
            </div>
        </div>

        <!-- 显示模块 -->
        <div class="mb-4">
            <label class="text-dark-400 text-xs mb-1 block">显示模块</label>
            <div class="flex flex-col gap-2">
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" checked class="accent-primary"> 结构事件
                </label>
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" checked class="accent-primary"> 订单块
                </label>
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" checked class="accent-primary"> FVG
                </label>
                <label class="flex items-center gap-2 text-sm text-dark-50 cursor-pointer">
                    <input type="checkbox" checked class="accent-primary"> 流动性池
                </label>
            </div>
        </div>

        <!-- 当前状态 -->
        <div class="bg-dark-800 rounded-lg p-3">
            <div class="text-primary font-bold text-sm mb-2">当前状态</div>
            <div class="text-xs text-dark-400 space-y-1">
                <div>价格: <span id="state-price" class="text-dark-50 font-bold">-</span></div>
                <div>Wyckoff: <span id="state-wyckoff" class="text-green-500">-</span></div>
                <div>趋势: <span id="state-trend" class="text-green-500">-</span></div>
                <div>OB: <span id="state-obs" class="text-dark-50">-</span></div>
                <div>FVG: <span id="state-fvgs" class="text-dark-50">-</span></div>
            </div>
        </div>
    </aside>

    <!-- 右侧图表区域 -->
    <div class="flex-1 bg-dark-900 p-4 flex flex-col">
        <!-- 当前回放信息 -->
        <div class="flex justify-between items-center mb-3">
            <div class="flex gap-3">
                <span id="current-time" class="bg-dark-800 text-primary px-2 py-1 rounded text-sm font-bold">-</span>
                <span id="current-price" class="bg-dark-800 text-green-500 px-2 py-1 rounded text-sm font-mono">-</span>
                <span id="bar-count" class="bg-dark-800 text-dark-400 px-2 py-1 rounded text-sm">-</span>
            </div>
        </div>

        <!-- K 线图 -->
        <div id="replay-chart" class="flex-1 bg-dark-800 rounded-lg relative"></div>

        <!-- 事件时间线 -->
        <div class="mt-3 bg-dark-800 rounded-lg p-3">
            <div class="text-primary font-bold text-sm mb-2">事件时间线</div>
            <div id="event-timeline" class="flex gap-4 overflow-x-auto pb-1">
                <div class="text-dark-400 text-sm">等待回放开始...</div>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script src="/static/js/chart.js"></script>
<script src="/static/js/replay.js"></script>
{% endblock %}
```

- [ ] **Step 2: 创建 replay.js**

```javascript
// pa_assistant/web/static/js/replay.js

class ReplayController {
    constructor() {
        this.chart = new PAChart('replay-chart');
        this.ws = null;
        this.isPlaying = false;
        this.currentBarIndex = 0;
        this.totalBars = 0;
        this.events = [];

        this.initControls();
    }

    initControls() {
        // Play/Pause
        document.getElementById('btn-play').addEventListener('click', () => {
            if (this.isPlaying) {
                this.pause();
            } else {
                this.play();
            }
        });

        // Previous/Next
        document.getElementById('btn-prev').addEventListener('click', () => this.stepBackward());
        document.getElementById('btn-next').addEventListener('click', () => this.stepForward());

        // Speed slider
        document.getElementById('replay-speed').addEventListener('input', (e) => {
            const speed = parseInt(e.target.value);
            document.getElementById('speed-value').textContent = `${speed}x`;
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'set_speed', speed }));
            }
        });

        // Progress bar
        document.getElementById('progress-bar').addEventListener('input', (e) => {
            const pct = parseInt(e.target.value);
            const barIndex = Math.floor(pct / 100 * this.totalBars);
            this.seekTo(barIndex);
        });

        // Timeframe selector
        document.querySelectorAll('.tf-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tf-btn').forEach(b => {
                    b.classList.remove('bg-primary', 'text-dark-900', 'font-bold');
                    b.classList.add('bg-dark-800', 'text-dark-400');
                });
                btn.classList.remove('bg-dark-800', 'text-dark-400');
                btn.classList.add('bg-primary', 'text-dark-900', 'font-bold');
                document.getElementById('replay-tf').value = btn.dataset.tf;
            });
        });

        // Mode selector
        document.querySelectorAll('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.mode-btn').forEach(b => {
                    b.classList.remove('bg-primary', 'text-dark-900', 'font-bold');
                    b.classList.add('bg-dark-800', 'text-dark-400');
                });
                btn.classList.remove('bg-dark-800', 'text-dark-400');
                btn.classList.add('bg-primary', 'text-dark-900', 'font-bold');
            });
        });
    }

    connect() {
        const tf = document.getElementById('replay-tf').value;
        const startTime = document.getElementById('start-time').value;
        const speed = document.getElementById('replay-speed').value;

        const wsUrl = `ws://${window.location.host}/ws/replay?timeframe=${tf}&start=${startTime}&speed=${speed}`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
        };

        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.isPlaying = false;
            this.updatePlayButton();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    handleMessage(data) {
        switch (data.type) {
            case 'init':
                this.totalBars = data.total_bars;
                this.currentBarIndex = 0;
                document.getElementById('progress-end').textContent = data.end_time;
                this.chart.setData([]);
                break;

            case 'bar':
                this.chart.addBar(data.bar);
                this.currentBarIndex = data.bar_index;
                this.updateProgress();
                this.updateCurrentInfo(data);
                if (data.analysis) {
                    this.updateState(data.analysis);
                }
                break;

            case 'event':
                this.addEvent(data.event);
                break;
        }
    }

    play() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.connect();
            // Wait for connection, then play
            setTimeout(() => {
                this.ws.send(JSON.stringify({ type: 'resume' }));
            }, 500);
        } else {
            this.ws.send(JSON.stringify({ type: 'resume' }));
        }
        this.isPlaying = true;
        this.updatePlayButton();
    }

    pause() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'pause' }));
        }
        this.isPlaying = false;
        this.updatePlayButton();
    }

    stepForward() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'step_forward' }));
        }
    }

    stepBackward() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'step_backward' }));
        }
    }

    seekTo(barIndex) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'seek', bar_index: barIndex }));
        }
    }

    updateProgress() {
        const pct = Math.floor(this.currentBarIndex / this.totalBars * 100);
        document.getElementById('progress-bar').value = pct;
        document.getElementById('progress-pct').textContent = `${pct}%`;
    }

    updateCurrentInfo(data) {
        document.getElementById('current-time').textContent = data.bar.timestamp;
        document.getElementById('current-price').textContent = `$${data.bar.close.toLocaleString()}`;
        document.getElementById('bar-count').textContent = `第 ${data.bar_index} 根 / ${this.totalBars} 根`;
    }

    updateState(analysis) {
        document.getElementById('state-price').textContent = `$${analysis.price.toLocaleString()}`;
        document.getElementById('state-wyckoff').textContent = analysis.wyckoff_phase || '-';
        document.getElementById('state-trend').textContent = analysis.trend || '-';
        document.getElementById('state-obs').textContent = `${analysis.active_obs || 0} 个生效`;
        document.getElementById('state-fvgs').textContent = `${analysis.active_fvgs || 0} 个未填补`;
    }

    addEvent(event) {
        this.events.push(event);
        const timeline = document.getElementById('event-timeline');
        const color = event.side === 'bullish' ? 'text-green-500' : 'text-red-500';

        timeline.innerHTML += `
            <div class="min-w-[100px] text-center">
                <div class="text-xs text-dark-400">${event.timestamp}</div>
                <div class="${color} text-xs font-bold">${event.text}</div>
            </div>
        `;

        // Auto-scroll to right
        timeline.scrollLeft = timeline.scrollWidth;
    }

    updatePlayButton() {
        const btn = document.getElementById('btn-play');
        if (this.isPlaying) {
            btn.textContent = '⏸ 暂停';
            btn.classList.remove('bg-primary');
            btn.classList.add('bg-red-500');
        } else {
            btn.textContent = '▶ 播放';
            btn.classList.remove('bg-red-500');
            btn.classList.add('bg-primary');
        }
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    window.replayController = new ReplayController();
});
```

- [ ] **Step 3: 创建 WebSocket 路由 ws.py**

```python
# pa_assistant/web/routes/ws.py
"""WebSocket routes for real-time replay."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import duckdb
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pa_assistant.analysis import (
    analyze_wyckoff,
    compute_delta,
    detect_divergences,
    detect_fvgs,
    detect_liquidity_levels,
    detect_order_blocks,
    detect_structure_events,
    detect_swings,
    resample_ohlcv,
)
from pa_assistant.config import get_settings

router = APIRouter()


@router.websocket("/ws/replay")
async def replay_websocket(
    websocket: WebSocket,
    timeframe: str = "1h",
    start: str = "2026-05-01T00:00",
    speed: int = 3,
) -> None:
    """WebSocket endpoint for bar-by-bar replay."""
    await websocket.accept()

    settings = get_settings()

    # Load all data
    conn = duckdb.connect(str(settings.duckdb_path), read_only=True)
    try:
        klines = conn.execute(
            "SELECT open_time, open, high, low, close, volume, "
            "quote_volume, taker_buy_base "
            "FROM kline_1m WHERE symbol = 'BTCUSDT' ORDER BY open_time",
        ).pl()
    finally:
        conn.close()

    if klines.is_empty():
        await websocket.close(code=1008, reason="No data")
        return

    # Resample
    resampled = resample_ohlcv(klines, timeframe)

    # Find start index
    start_dt = datetime.fromisoformat(start)
    start_idx = 0
    for i, row in enumerate(resampled.iter_rows(named=True)):
        if row["open_time"] >= start_dt:
            start_idx = i
            break

    # Send init message
    await websocket.send_json({
        "type": "init",
        "total_bars": resampled.height - start_idx,
        "start_time": str(resampled.row(start_idx, named=True)["open_time"]),
        "end_time": str(resampled.row(resampled.height - 1, named=True)["open_time"]),
    })

    # Replay loop
    is_playing = False
    current_idx = start_idx
    delay = 1.0 / speed

    try:
        while True:
            # Check for control messages (non-blocking)
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.01)
                data = json.loads(msg)

                if data["type"] == "resume":
                    is_playing = True
                elif data["type"] == "pause":
                    is_playing = False
                elif data["type"] == "step_forward":
                    current_idx = min(current_idx + 1, resampled.height - 1)
                elif data["type"] == "step_backward":
                    current_idx = max(current_idx - 1, start_idx)
                elif data["type"] == "seek":
                    current_idx = max(start_idx, min(data["bar_index"] + start_idx, resampled.height - 1))
                elif data["type"] == "set_speed":
                    delay = 1.0 / data["speed"]
            except asyncio.TimeoutError:
                pass

            # Send bar if playing
            if is_playing and current_idx < resampled.height:
                row = resampled.row(current_idx, named=True)

                # Run analysis on data up to current bar
                subset = resampled.slice(0, current_idx + 1)
                annotated = detect_swings(subset, lookback=3)
                structure_events = detect_structure_events(annotated)
                liquidity_levels = detect_liquidity_levels(subset)
                order_blocks = detect_order_blocks(subset, structure_events)
                fvgs = detect_fvgs(subset)

                await websocket.send_json({
                    "type": "bar",
                    "bar_index": current_idx - start_idx,
                    "bar": {
                        "timestamp": str(row["open_time"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    },
                    "analysis": {
                        "price": float(row["close"]),
                        "wyckoff_phase": "neutral",
                        "trend": "none",
                        "active_obs": sum(1 for o in order_blocks if o.mitigated_at is None),
                        "active_fvgs": sum(1 for f in fvgs if f.mitigated_at is None),
                    },
                })

                current_idx += 1
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
```

- [ ] **Step 4: 更新 app.py 注册 WebSocket 路由**

```python
# pa_assistant/web/app.py
"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pa_assistant.web.routes import api, pages, ws

BASE_DIR = Path(__file__).parent

app = FastAPI(title="PA Trading Assistant", version="0.1.0")

# Mount static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Templates
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Include routes
app.include_router(pages.router)
app.include_router(api.router)
app.include_router(ws.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
```

- [ ] **Step 5: 验证 WebSocket**

Run: 启动服务后在浏览器打开回测页面，点击「播放」
Expected: K 线逐根显示，状态实时更新

- [ ] **Step 6: Commit**

```bash
git add pa_assistant/web/templates/backtest.html pa_assistant/web/static/js/replay.js pa_assistant/web/routes/ws.py pa_assistant/web/app.py
git commit -m "feat(web): 创建回测回放页 + WebSocket 实时推送"
```

---

## Task 8: CLI 启动命令

**Files:**
- Modify: `pa_assistant/cli.py`

- [ ] **Step 1: 添加 serve 命令**

在 `pa_assistant/cli.py` 中添加：

```python
@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, help="Enable auto-reload for development."),
) -> None:
    """Start the web server."""
    import uvicorn

    typer.secho(
        f"Starting PA Assistant web server on {host}:{port}",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.echo(f"  Dashboard:  http://localhost:{port}/")
    typer.echo(f"  Liquidity:  http://localhost:{port}/liquidity")
    typer.echo(f"  Backtest:   http://localhost:{port}/backtest")
    typer.echo("")

    uvicorn.run(
        "pa_assistant.web.app:app",
        host=host,
        port=port,
        reload=reload,
    )
```

- [ ] **Step 2: 验证命令**

Run: `uv run pa serve --help`
Expected: 显示命令帮助信息

- [ ] **Step 3: Commit**

```bash
git add pa_assistant/cli.py
git commit -m "feat(web): 添加 pa serve 命令启动 Web 服务"
```

---

## Task 9: 测试

**Files:**
- Create: `tests/unit/test_web.py`

- [ ] **Step 1: 创建测试文件**

```python
# tests/unit/test_web.py
"""Tests for web module."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pa_assistant.web.app import app


@pytest.fixture
def client() -> TestClient:
    """Create test client."""
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_page(client: TestClient) -> None:
    """Test dashboard page loads."""
    response = client.get("/")
    assert response.status_code == 200
    assert "PA Assistant" in response.text


def test_liquidity_page(client: TestClient) -> None:
    """Test liquidity page loads."""
    response = client.get("/liquidity")
    assert response.status_code == 200
    assert "流动性" in response.text


def test_backtest_page(client: TestClient) -> None:
    """Test backtest page loads."""
    response = client.get("/backtest")
    assert response.status_code == 200
    assert "回放" in response.text


def test_klines_api(client: TestClient) -> None:
    """Test klines API endpoint."""
    response = client.get("/api/klines?symbol=BTCUSDT&timeframe=1h&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert "bars" in data
    assert "total" in data
```

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/unit/test_web.py -v`
Expected: 所有测试通过

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_web.py
git commit -m "test(web): 添加 Web 模块单元测试"
```

---

## Task 10: 最终集成测试

- [ ] **Step 1: 启动服务**

Run: `uv run pa serve`
Expected: 服务启动，显示访问地址

- [ ] **Step 2: 访问仪表盘**

打开 http://localhost:8000/
Expected: 看到深色主题仪表盘，K 线图正常显示

- [ ] **Step 3: 运行分析**

点击「运行分析」按钮
Expected: 右侧显示分析结果，图表叠加 OB/FVG/流动性池

- [ ] **Step 4: 访问流动性页**

打开 http://localhost:8000/liquidity
Expected: 看到流动性池列表和阶梯图

- [ ] **Step 5: 访问回测页**

打开 http://localhost:8000/backtest
Expected: 看到回放控制面板

- [ ] **Step 6: 运行 lint + typecheck**

Run: `make check`
Expected: 无错误

- [ ] **Step 7: Final Commit**

```bash
git add -A
git commit -m "feat: Web 端完成 - 仪表盘 + 流动性分析 + 回测回放"
```

---

## 验收清单

- [ ] 主仪表盘可运行分析并显示结果
- [ ] 参数调整后图表和结果实时更新
- [ ] 流动性分析页正确显示池列表和阶梯图
- [ ] 回放功能可逐根推进并显示分析结果
- [ ] 深色主题一致，无对比度问题
- [ ] 所有 API 有错误处理
- [ ] 无 lint/typecheck 错误
- [ ] 测试通过

---

## 依赖关系

```
Task 1 (依赖配置)
    ↓
Task 2 (模块骨架)
    ↓
Task 3 (基础模板) ← Task 4 (仪表盘页面)
    ↓
Task 5 (分析 API) ← Task 6 (流动性页)
    ↓
Task 7 (回测页) ← Task 8 (CLI 命令)
    ↓
Task 9 (测试) ← Task 10 (集成测试)
```
