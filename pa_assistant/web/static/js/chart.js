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

        // Handle resize
        this.resizeObserver = new ResizeObserver(() => {
            this.chart.applyOptions({
                width: this.container.clientWidth,
                height: this.container.clientHeight,
            });
        });
        this.resizeObserver.observe(this.container);
    }

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

    clearLines() {
        this.lines.forEach(line => this.candleSeries.removePriceLine(line));
        this.lines = [];
    }

    setMarkers(markers) {
        this.candleSeries.setMarkers(markers.map(m => ({
            time: m.timestamp,
            position: m.side === 'bullish' ? 'belowBar' : 'aboveBar',
            color: m.side === 'bullish' ? '#22C55E' : '#EF4444',
            shape: m.side === 'bullish' ? 'arrowUp' : 'arrowDown',
            text: m.text,
        })));
    }

    fitContent() {
        this.chart.timeScale().fitContent();
    }

    destroy() {
        this.resizeObserver.disconnect();
        this.chart.remove();
    }
}

window.PAChart = PAChart;
