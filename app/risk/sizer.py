"""Position sizing: risk-based with a buying-power cap."""

import math
from typing import Optional


def position_size(
    *,
    capital: float,
    entry: float,
    stop: Optional[float] = None,
    risk_per_trade_pct: float = 0.01,
    leverage: float = 1.0,
    confidence: float = 1.0,
    lot_size: int = 1,
    min_stop_distance_pct: float = 0.001,
) -> int:
    """
    qty = min(risk budget / per-share risk, buying power / entry), scaled by confidence.

    - Risk budget: capital * risk_per_trade_pct * confidence.
    - Per-share risk is floored at `min_stop_distance_pct` of entry so a very tight stop
      cannot produce an outsized position.
    - Without a stop, size is purely by buying power (capital * leverage * confidence).
    """
    if capital <= 0 or entry <= 0:
        return 0
    confidence = max(0.0, min(confidence, 2.0))
    by_capital = capital * leverage * min(confidence, 1.0) / entry

    if stop is not None and stop > 0:
        per_share_risk = max(abs(entry - stop), entry * min_stop_distance_pct)
        by_risk = capital * risk_per_trade_pct * confidence / per_share_risk
        raw = min(by_risk, by_capital)
    else:
        raw = by_capital

    lot = max(1, lot_size)
    return int(math.floor(raw / lot) * lot)
