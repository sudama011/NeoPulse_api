"""Parsing for operator commands (pure functions, no I/O)."""

from dataclasses import dataclass
from typing import Optional

from app.domain.models import ProductType, Side

ORDER_USAGE = (
    "Usage: /buy SYMBOL QTY [@LIMIT] [sl=PRICE] [tgt=PRICE] [cnc]\n"
    "  /buy RELIANCE 10                 market, intraday\n"
    "  /buy RELIANCE 10 @2450 sl=2420   limit with stop-loss\n"
    "  /sell SBIN 50 sl=842 tgt=815     short (intraday)"
)


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class OrderIntent:
    side: Side
    symbol: str
    qty: int
    limit_price: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    product: ProductType = ProductType.MIS

    def describe(self) -> str:
        kind = f"LIMIT @ {self.limit_price:.2f}" if self.limit_price else "MARKET"
        parts = [f"{self.side.value} {self.qty} {self.symbol} {kind} ({self.product.value})"]
        if self.stop:
            parts.append(f"SL {self.stop:.2f}")
        if self.target:
            parts.append(f"TGT {self.target:.2f}")
        return " · ".join(parts)


def _price(raw: str, name: str) -> float:
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        raise CommandError(f"{name} must be a number, got '{raw}'") from None
    if value <= 0:
        raise CommandError(f"{name} must be positive")
    return value


def parse_order(side: Side, args: list[str]) -> OrderIntent:
    if len(args) < 2:
        raise CommandError(ORDER_USAGE)
    symbol = args[0].upper()
    try:
        qty = int(args[1])
    except ValueError:
        raise CommandError(f"quantity must be a whole number, got '{args[1]}'\n\n{ORDER_USAGE}") from None
    if qty <= 0:
        raise CommandError("quantity must be positive")

    limit = stop = target = None
    product = ProductType.MIS
    for token in args[2:]:
        t = token.lower()
        if t in ("cnc", "mis"):
            product = ProductType(t.upper())
        elif t.startswith(("sl=", "stop=")):
            stop = _price(t.split("=", 1)[1], "sl")
        elif t.startswith(("tgt=", "target=", "tp=")):
            target = _price(t.split("=", 1)[1], "tgt")
        elif t.startswith("@"):
            limit = _price(t[1:], "limit price")
        else:
            try:
                limit = _price(t, "limit price")
            except CommandError:
                raise CommandError(f"Unrecognised argument '{token}'\n\n{ORDER_USAGE}") from None

    ref = limit
    if ref is not None:
        if stop is not None and (stop >= ref if side is Side.BUY else stop <= ref):
            raise CommandError(f"sl {stop} must be {'below' if side is Side.BUY else 'above'} the entry {ref}")
        if target is not None and (target <= ref if side is Side.BUY else target >= ref):
            raise CommandError(f"tgt {target} must be {'above' if side is Side.BUY else 'below'} the entry {ref}")
    if stop is not None and target is not None and (stop >= target if side is Side.BUY else stop <= target):
        raise CommandError("sl and tgt are on the wrong sides")
    return OrderIntent(side, symbol, qty, limit, stop, target, product)


PROTECT_USAGE = "Usage: /sl SYMBOL [sl=PRICE] [tgt=PRICE]   (0 clears a level, omitted levels are unchanged)"


def parse_protection(args: list[str]) -> tuple[str, dict[str, Optional[float]]]:
    """Returns (symbol, updates) where updates has 'stop'/'target' keys only for levels given."""
    if len(args) < 2:
        raise CommandError(PROTECT_USAGE)
    updates: dict[str, Optional[float]] = {}
    for token in args[1:]:
        key, _, raw = token.lower().partition("=")
        try:
            value = float(raw)
        except ValueError:
            raise CommandError(f"'{token}' is not KEY=PRICE\n{PROTECT_USAGE}") from None
        if value < 0:
            raise CommandError("prices cannot be negative")
        if key in ("sl", "stop"):
            updates["stop"] = value or None
        elif key in ("tgt", "target", "tp"):
            updates["target"] = value or None
        else:
            raise CommandError(f"Unrecognised argument '{token}'\n{PROTECT_USAGE}")
    return args[0].upper(), updates
