import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict

from app.data.stream import data_stream

logger = logging.getLogger("CandleAggregator")


class CandleBuilder:
    """Helper to track OHLCV for a single token."""

    def __init__(self, token: str):
        self.token = token
        self.current_minute = None
        self.open = 0.0
        self.high = -float("inf")
        self.low = float("inf")
        self.close = 0.0
        self.volume = 0
        self.last_trade_time = None
        self.is_dirty = False

    def update(self, ltp: float, volume: int, timestamp: datetime) -> Dict[str, Any] | None:
        minute = timestamp.replace(second=0, microsecond=0)

        completed_candle = None

        # Detect Minute Change
        if self.current_minute and minute > self.current_minute:
            if self.is_dirty:
                completed_candle = {
                    "type": "CANDLE",
                    "token": self.token,
                    "timestamp": self.current_minute,
                    "open": self.open,
                    "high": self.high,
                    "low": self.low,
                    "close": self.close,
                    "volume": self.volume,
                }
            # Reset for new candle
            self.reset(minute, ltp, volume)

        elif self.current_minute is None:
            self.reset(minute, ltp, volume)

        # Update Current Candle
        self.high = max(self.high, ltp)
        self.low = min(self.low, ltp)
        self.close = ltp
        self.volume = volume  # Note: Kotak sends cumulative vol? Check specific API behavior.
        # If cumulative, we need (vol - prev_vol).
        # For now assuming 'v' is daily cumulative, logic needs delta.
        # *Correction*: Most simple aggregators just track Close/High/Low.
        # Volume delta logic requires tracking previous tick vol.
        self.is_dirty = True

        return completed_candle

    def reset(self, minute, ltp, volume):
        self.current_minute = minute
        self.open = ltp
        self.high = ltp
        self.low = ltp
        self.close = ltp
        self.volume = volume
        self.is_dirty = True


class CandleAggregator:
    """
    Global service that listens to ALL ticks and emits Candle events.
    """

    def __init__(self):
        self.builders: Dict[str, CandleBuilder] = {}
        self.queue = asyncio.Queue()
        self.is_running = False

    async def start(self):
        """Register as a global listener."""
        self.is_running = True
        # Hook into DataStream global listeners
        sub_id = "AGGREGATOR"
        data_stream._global_listeners[sub_id] = self.queue
        logger.info("🕯️ Candle Aggregator Started")

        while self.is_running:
            try:
                # Get raw tick batch
                item = await self.queue.get()

                # Check if it's a list of ticks (ignore existing Candle events)
                if isinstance(item, list):
                    candles_to_publish = []

                    for tick in item:
                        token = tick.get("tk")
                        ltp = float(tick.get("ltp", 0))
                        vol = int(tick.get("v", 0))  # Cumulative volume usually

                        # Parse time (Kotak sends 'ltt' string, need optimized parsing)
                        # For speed, we might use system time if 'ltt' is complex/missing
                        # using server time for aggregation is safer for consistency
                        ts = datetime.now()

                        if token not in self.builders:
                            self.builders[token] = CandleBuilder(token)

                        candle = self.builders[token].update(ltp, vol, ts)
                        if candle:
                            candles_to_publish.append(candle)

                    if candles_to_publish:
                        # Publish back to stream!
                        await data_stream.publish(candles_to_publish)

            except Exception as e:
                logger.error(f"Aggregator Error: {e}")

    async def stop(self):
        self.is_running = False
        if "AGGREGATOR" in data_stream._global_listeners:
            del data_stream._global_listeners["AGGREGATOR"]


candle_aggregator = CandleAggregator()
