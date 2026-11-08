from app.domain.models import Candle
from app.strategy import register_strategy
from app.strategy.base import Strategy


@register_strategy("MANUAL")
class ManualStrategy(Strategy):
    """Operator-driven: orders come from Telegram/API; stops, targets and square-off still apply."""

    warmup = 0

    async def on_candle(self, candle: Candle) -> None:
        """No automated logic."""
