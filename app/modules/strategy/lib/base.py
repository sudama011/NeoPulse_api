from abc import ABC, abstractmethod
import logging

class BaseStrategy(ABC):
    def __init__(self, strategy_id: str, symbol: str):
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.logger = logging.getLogger(f"Strat-{strategy_id}")
        
        # Local State (Reset daily)
        self.position = 0  # Current Net Qty
        self.pnl = 0.0     # Running PnL
        self.trades = []

    @abstractmethod
    async def on_tick(self, tick_data: dict):
        """
        Called every time a new tick arrives.
        Must be implemented by the child strategy.
        """
        pass

    @abstractmethod
    async def on_order_update(self, order_data: dict):
        """
        Called when an order is filled/rejected.
        """
        pass

    def log(self, message):
        self.logger.info(f"[{self.symbol}] {message}")