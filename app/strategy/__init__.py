"""
Strategy Registry — Plug-and-Play Pattern.

To add a new strategy:
1. Create a file in app/strategy/ (e.g., my_strategy.py)
2. Subclass BaseStrategy
3. Decorate with @register_strategy("MY_STRATEGY")

That's it. The engine will find it automatically.

Example:
    from app.strategy import register_strategy
    from app.strategy.base import BaseStrategy

    @register_strategy("GOLDEN_CROSS")
    class GoldenCrossStrategy(BaseStrategy):
        async def on_tick(self, tick):
            ...
"""

import importlib
import logging
import pkgutil
from typing import Dict, Type

logger = logging.getLogger("StrategyRegistry")

# --- The Registry ---
_STRATEGY_REGISTRY: Dict[str, Type] = {}


def register_strategy(name: str):
    """Decorator to register a strategy class by name."""

    def decorator(cls):
        key = name.upper()
        if key in _STRATEGY_REGISTRY:
            logger.warning(f"⚠️ Strategy '{key}' already registered. Overwriting with {cls.__name__}.")
        _STRATEGY_REGISTRY[key] = cls
        logger.debug(f"📝 Registered strategy: {key} -> {cls.__name__}")
        return cls

    return decorator


def get_strategy_class(name: str) -> Type:
    """Lookup a strategy class by registered name."""
    key = name.upper()
    if key not in _STRATEGY_REGISTRY:
        available = ", ".join(sorted(_STRATEGY_REGISTRY.keys())) or "(none)"
        raise ValueError(f"Unknown strategy '{key}'. Available: {available}")
    return _STRATEGY_REGISTRY[key]


def list_strategies() -> Dict[str, str]:
    """Returns {name: class_name} for all registered strategies."""
    return {k: v.__name__ for k, v in sorted(_STRATEGY_REGISTRY.items())}


def _auto_discover():
    """Import all modules in this package so @register_strategy decorators execute."""
    package_path = __path__
    for _importer, modname, _ispkg in pkgutil.iter_modules(package_path):
        if modname.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{modname}")
        except Exception as e:
            logger.error(f"❌ Failed to import strategy module '{modname}': {e}")


# Auto-discover on package import
_auto_discover()
