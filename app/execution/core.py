from abc import ABC, abstractmethod
from typing import Dict, Any

class BrokerAdapter(ABC):
    """
    Abstract Base Class for all Brokers (Real & Virtual).
    Strategies interact ONLY with this interface, never implementation details.
    """
    
    @abstractmethod
    async def login(self) -> None:
        """Establish session."""
        pass

    @abstractmethod
    async def place_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place an order.
        Must return standard dict: {'nOrdNo': '123', 'status': 'success'}
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an open order."""
        pass

    @abstractmethod
    async def get_positions(self) -> Dict[str, Any]:
        """Fetch open positions."""
        pass

    @abstractmethod
    async def get_limits(self) -> Dict[str, Any]:
        """Fetch available margin/capital."""
        pass