"""
Strategy registry.

Add a strategy by creating a module in `app/strategy/library/`:

    from app.strategy import register_strategy
    from app.strategy.base import Strategy

    @register_strategy("MY_STRATEGY")
    class MyStrategy(Strategy):
        warmup = 30
        async def on_candle(self, candle): ...

Modules in the library package are imported automatically on first lookup.
"""

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.strategy.base import Strategy

logger = logging.getLogger("StrategyRegistry")

_REGISTRY: dict[str, type["Strategy"]] = {}
_discovered = False


def register_strategy(name: str):
    def decorator(cls):
        key = name.upper()
        if key in _REGISTRY and _REGISTRY[key] is not cls:
            raise ValueError(f"Strategy name '{key}' already registered by {_REGISTRY[key].__name__}")
        _REGISTRY[key] = cls
        return cls

    return decorator


def _discover() -> None:
    global _discovered
    if _discovered:
        return
    from app.strategy import library

    for mod in pkgutil.iter_modules(library.__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{library.__name__}.{mod.name}")  # errors surface loudly
    _discovered = True


def get_strategy_class(name: str) -> type["Strategy"]:
    _discover()
    key = name.upper()
    if key not in _REGISTRY:
        raise ValueError(f"Unknown strategy '{key}'. Available: {', '.join(sorted(_REGISTRY)) or '(none)'}")
    return _REGISTRY[key]


def list_strategies() -> dict[str, str]:
    _discover()
    return {k: (v.__doc__ or v.__name__).strip().splitlines()[0] for k, v in sorted(_REGISTRY.items())}
