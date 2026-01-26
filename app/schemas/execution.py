from enum import Enum
from typing import Optional

from pydantic import BaseModel


class OrderStatus(str, Enum):
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL_FILL"
    FAILED = "FAILED"


class OrderResponse(BaseModel):
    """
    Standardized response from ANY broker (Real or Virtual).
    """

    order_id: str
    status: OrderStatus
    filled_qty: int = 0
    average_price: float = 0.0
    error_message: Optional[str] = None
    raw_response: dict = {}
