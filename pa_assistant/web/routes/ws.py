"""WebSocket routes for real-time replay."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime

import duckdb
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pa_assistant.analysis import (
    detect_fvgs,
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

    resampled = resample_ohlcv(klines, timeframe)

    start_dt = datetime.fromisoformat(start)
    start_idx = 0
    for i, row in enumerate(resampled.iter_rows(named=True)):
        if row["open_time"] >= start_dt:
            start_idx = i
            break

    await websocket.send_json({
        "type": "init",
        "total_bars": resampled.height - start_idx,
        "start_time": str(resampled.row(start_idx, named=True)["open_time"]),
        "end_time": str(resampled.row(resampled.height - 1, named=True)["open_time"]),
    })

    is_playing = False
    current_idx = start_idx
    delay = 1.0 / speed

    try:
        while True:
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
            except TimeoutError:
                pass

            if is_playing and current_idx < resampled.height:
                row = resampled.row(current_idx, named=True)

                subset = resampled.slice(0, current_idx + 1)
                annotated = detect_swings(subset, lookback=3)
                structure_events = detect_structure_events(annotated)
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
        with contextlib.suppress(Exception):
            await websocket.close()
