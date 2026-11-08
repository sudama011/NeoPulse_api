from app.domain.models import Candle, Tick
from app.strategy import register_strategy
from app.strategy.base import Strategy


@register_strategy("GRID_ACCUMULATOR")
class GridAccumulatorStrategy(Strategy):
    """Long-only averaging grid: buy more on each grid step down, exit all at a per-share profit.

    Params (defaults): initial_qty=1, qty_step=1 (arithmetic size increase per level),
    grid_step=0.10 (Rs drop per level), profit_per_share=0.40, max_levels=10,
    max_qty=None (hard cap on total quantity).
    Grid progress is kept in persisted state, so a restart resumes the same grid.
    """

    warmup = 0

    def setup(self) -> None:
        p = self.params
        self.initial_qty = int(p.get("initial_qty", 1))
        self.qty_step = int(p.get("qty_step", 1))
        self.grid_step = float(p.get("grid_step", 0.10))
        self.profit_per_share = float(p.get("profit_per_share", 0.40))
        self.max_levels = int(p.get("max_levels", 10))
        self.max_qty = int(p["max_qty"]) if p.get("max_qty") else None

    async def on_candle(self, candle: Candle) -> None:
        """Grid logic is tick driven."""

    async def on_tick(self, tick: Tick) -> None:
        if self.ctx.has_pending_order or self.position < 0:
            return
        ltp = tick.ltp
        levels = int(self.state.get("levels", 0))

        if self.position == 0:
            if levels:  # grid completed (or position closed externally): start over
                self.state.update(levels=0, last_level_price=None)
            if await self.buy(ltp, qty=self.initial_qty, reason="GRID_L1"):
                self.state.update(levels=1, last_level_price=ltp)
            return

        if ltp - self.avg_price >= self.profit_per_share:
            await self.exit("GRID_TARGET")
            return

        last = self.state.get("last_level_price") or self.avg_price
        next_level = last - self.grid_step
        if levels >= self.max_levels or ltp > next_level:
            return
        qty = self.initial_qty + levels * self.qty_step
        if self.max_qty is not None:
            qty = min(qty, self.max_qty - self.position)
        if qty <= 0:
            return
        if await self.buy(ltp, qty=qty, reason=f"GRID_L{levels + 1}"):
            # Anchor to the theoretical level (not ltp) so the grid spacing stays exact.
            self.state.update(levels=levels + 1, last_level_price=round(next_level, 2))
