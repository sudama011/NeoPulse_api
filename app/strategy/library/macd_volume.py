from app.domain.models import Candle
from app.strategy import register_strategy
from app.strategy.base import Strategy
from app.strategy.indicators import tb


@register_strategy("MACD_VOLUME")
class MACDVolumeStrategy(Strategy):
    """Long-only trend follower: MACD histogram cross-up above EMA trend, ATR stop/target.

    Params (defaults): ema_period=200, macd_fast=12, macd_slow=26, macd_signal=9,
    vol_period=20, vol_multiplier=1.2, atr_period=14, atr_stop_mult=2.0, reward_risk=2.0.
    High relative volume -> full size; otherwise half size.
    """

    def setup(self) -> None:
        p = self.params
        self.ema_period = int(p.get("ema_period", 200))
        self.fast = int(p.get("macd_fast", 12))
        self.slow = int(p.get("macd_slow", 26))
        self.signal = int(p.get("macd_signal", 9))
        self.vol_period = int(p.get("vol_period", 20))
        self.vol_mult = float(p.get("vol_multiplier", 1.2))
        self.atr_period = int(p.get("atr_period", 14))
        self.atr_stop_mult = float(p.get("atr_stop_mult", 2.0))
        self.reward_risk = float(p.get("reward_risk", 2.0))
        self.warmup = max(self.ema_period, self.slow + self.signal, self.vol_period, self.atr_period) + 2

    async def on_candle(self, candle: Candle) -> None:
        closes = [c.close for c in self.candles]
        _, _, hist_now = tb.macd(closes, self.fast, self.slow, self.signal)
        _, _, hist_prev = tb.macd(closes[:-1], self.fast, self.slow, self.signal)

        if self.position > 0:
            if hist_prev > 0 >= hist_now:
                await self.exit("MACD_CROSS_DOWN")
            return
        if self.position < 0 or self.ctx.has_pending_order:
            return

        crossed_up = hist_prev < 0 <= hist_now
        if not crossed_up or candle.close <= tb.ema(closes, self.ema_period):
            return

        volumes = [float(c.volume) for c in self.candles]
        vol_avg = tb.sma(volumes[:-1], self.vol_period)
        high_volume = vol_avg > 0 and candle.volume > vol_avg * self.vol_mult

        atr = tb.atr([c.to_dict() for c in self.candles], self.atr_period)
        if atr <= 0:
            return
        risk = atr * self.atr_stop_mult
        await self.buy(
            candle.close,
            stop=candle.close - risk,
            target=candle.close + risk * self.reward_risk,
            confidence=1.0 if high_volume else 0.5,
            reason="MACD_CROSS_UP" + ("_HIVOL" if high_volume else ""),
        )
