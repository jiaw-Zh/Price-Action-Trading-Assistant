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
        document.getElementById('btn-play').addEventListener('click', () => {
            if (this.isPlaying) {
                this.pause();
            } else {
                this.play();
            }
        });

        document.getElementById('btn-prev').addEventListener('click', () => this.stepBackward());
        document.getElementById('btn-next').addEventListener('click', () => this.stepForward());

        document.getElementById('replay-speed').addEventListener('input', (e) => {
            const speed = parseInt(e.target.value);
            document.getElementById('speed-value').textContent = `${speed}x`;
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'set_speed', speed }));
            }
        });

        document.getElementById('progress-bar').addEventListener('input', (e) => {
            const pct = parseInt(e.target.value);
            const barIndex = Math.floor(pct / 100 * this.totalBars);
            this.seekTo(barIndex);
        });

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
            setTimeout(() => {
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'resume' }));
                }
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
        const pct = this.totalBars > 0 ? Math.floor(this.currentBarIndex / this.totalBars * 100) : 0;
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

document.addEventListener('DOMContentLoaded', () => {
    window.replayController = new ReplayController();
});
