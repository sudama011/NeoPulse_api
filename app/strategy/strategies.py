import logging
from collections import deque
from typing import Dict, Any

from app.strategy.base import BaseStrategy
from app.strategy.toolbox import tb

logger = logging.getLogger("Strategies")

class MACDVolumeStrategy(BaseStrategy):
    """
    Production-Grade Intraday Strategy.
    
    Logic:
    1. Trend Filter: Price > EMA 200 (The "River")
    2. Momentum: MACD Crossover (The "Trigger")
    3. Confidence: Volume > 1.2x Average (The "Fuel") -> Doubles Position Size
    4. Safety: ATR-based Stop Loss
    """

    def __init__(self, name: str, symbol: str, token: str, params: Dict[str, Any]):
        super().__init__(name, symbol, token, params)
        
        # --- 1. Configuration (Loaded from DB) ---
        self.ema_period = params.get("ema_period", 200)
        self.fast = params.get("macd_fast", 12)
        self.slow = params.get("macd_slow", 26)
        self.signal = params.get("macd_signal", 9)
        self.vol_period = params.get("vol_period", 20)
        self.vol_mult = params.get("vol_multiplier", 1.2)
        self.atr_period = params.get("atr_period", 14)
        
        # --- 2. Data Buffers (State) ---
        # We need enough history to calculate EMA 200
        self.history_len = 300 
        self.closes = deque(maxlen=self.history_len)
        self.highs = deque(maxlen=self.history_len)
        self.lows = deque(maxlen=self.history_len)
        self.volumes = deque(maxlen=self.history_len)
        
        # Track previous MACD histogram to detect crossovers
        self.macd_hist = deque(maxlen=3) 

    async def on_tick(self, tick: Dict[str, Any]):
        """
        Processed on every single tick.
        """
        try:
            # A. Parse Tick
            ltp = float(tick.get("ltp", 0.0))
            if ltp <= 0: return

            # Handle Volume (Live vs Backtest compatibility)
            # Live feeds sometimes send 'v', 'vol', or 'volume'
            vol = float(tick.get("volume", tick.get("v", tick.get("vol", 0.0))))
            
            # OHLC Extraction (Backtest sends full candle, Live sends LTP)
            # In Live, we approximate High/Low with LTP until we have a candle builder
            ohlc = tick.get("_ohlc", {})
            h = float(ohlc.get("high", ltp))
            l = float(ohlc.get("low", ltp))
            
            # B. Update History
            self.closes.append(ltp)
            self.highs.append(h)
            self.lows.append(l)
            self.volumes.append(vol)
            
            # Wait for warmup (Need 200 points for EMA)
            if len(self.closes) < self.ema_period:
                return

            # C. Calculate Indicators (Using Vectorized Toolbox)
            # Note: We pass list(deque) because toolbox expects lists
            closes_list = list(self.closes)
            
            # 1. Trend
            ema_trend = tb.ema(closes_list, self.ema_period)
            is_uptrend = ltp > ema_trend
            
            # 2. Momentum (MACD)
            macd_line, sig_line, _ = tb.macd(closes_list, self.fast, self.slow, self.signal)
            current_hist = macd_line - sig_line
            self.macd_hist.append(current_hist)
            
            # 3. Volume
            vol_list = list(self.volumes)
            vol_avg = tb.sma(vol_list, self.vol_period)
            is_high_volume = vol > (vol_avg * self.vol_mult)

            # 4. Volatility (ATR)
            atr = tb.atr(
                [{"high": x, "low": y, "close": z} for x, y, z in zip(self.highs, self.lows, self.closes)], 
                self.atr_period
            )

            # D. Signal Logic

            # --- ENTRY (Long Only) ---
            if self.position == 0:
                # Check for Crossover: Prev Hist < 0 AND Curr Hist > 0
                if len(self.macd_hist) >= 2:
                    just_crossed_up = self.macd_hist[-2] < 0 and self.macd_hist[-1] > 0
                    
                    if just_crossed_up and is_uptrend:
                        # Confidence Sizing
                        # If Volume is high -> High Confidence (1.0) -> Risk Manager gives max size
                        # If Volume is low  -> Low Confidence (0.5)  -> Risk Manager gives half size
                        confidence = 1.0 if is_high_volume else 0.5
                        tag = "HIGH_CONF_ENTRY" if is_high_volume else "MED_CONF_ENTRY"
                        
                        logger.info(f"🚀 {self.symbol}: {tag} | MACD Cross | Vol: {vol:.0f}/{vol_avg:.0f}")

                        # Dynamic Stop Loss
                        sl_price = ltp - (atr * 2)
                        
                        await self.buy(price=ltp, sl=sl_price, confidence=confidence, tag=tag)

            # --- EXIT (Profit Taking / Stop) ---
            elif self.position > 0:
                # Standard Exit: MACD Crosses Down
                if len(self.macd_hist) >= 2:
                    just_crossed_down = self.macd_hist[-2] > 0 and self.macd_hist[-1] < 0
                    
                    if just_crossed_down:
                        logger.info(f"📉 {self.symbol}: MACD CROSS UNDER (Exit Signal)")
                        await self.sell(price=ltp, tag="MACD_EXIT")

        except Exception as e:
            logger.error(f"❌ Error in {self.name}: {e}")