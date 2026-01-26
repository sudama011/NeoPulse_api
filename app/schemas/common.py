from enum import Enum

from pydantic import BaseModel, Field


class OrderType(Enum):
    LIMIT = "L"
    MARKET = "MKT"
    STOP_LOSS = "SL"
    STOP_LOSS_MARKET = "SL-M"


class TransactionType(Enum):
    BUY = "B"
    SELL = "S"


class Validity(Enum):
    DAY = "DAY"
    IOC = "IOC"


class Product(Enum):
    MIS = "MIS"
    NORMAL = "NRML"
    CASH_AND_CARRY = "CNC"
    COVER_ORDER = "CO"
    BRACKET_ORDER = "BO"
    MARGIN_TRADING_FACILITY = "MTF"


class ExchangeSegment(Enum):
    NSE_CM = "nse_cm"
    BSE_CM = "bse_cm"
    MCX = "mcx_fo"
    NFO = "nse_fo"
    BFO = "bse_fo"


class OrderRequest(BaseModel):
    exchange_segment: ExchangeSegment = Field(default=ExchangeSegment.NSE_CM, description="Exchange Segment")
    product: Product = Field(default=Product.MIS, description="Product Type")
    price: float = Field(..., description="Order Price", example=1000.00)
    order_type: OrderType = Field(default=OrderType.MARKET, description="Order Type")
    quantity: int = Field(..., description="Quantity", example=1)
    validity: Validity = Field(default=Validity.DAY, description="Order Validity")
    trading_symbol: str = Field(..., description="Trading Symbol", example="RELIANCE")
    transaction_type: TransactionType = Field(default=TransactionType.BUY, description="Transaction Type")
    amo: str = Field(default="NO", description="AMO")
    disclosed_quantity: int = Field(default=0, description="Disclosed Quantity")
    market_protection: str = Field(default="0", description="Market Protection")
    pf: str = Field(default="N", description="PF")
    trigger_price: float = Field(default=0, description="Trigger Price")
    tag: str = Field(default=None, description="Tag")
    scrip_token: str = Field(default=None, description="Scrip Token")
    square_off_type: str = Field(default=None, description="Square Off Type")
    stop_loss_type: str = Field(default=None, description="Stop Loss Type")
    stop_loss_value: float = Field(default=None, description="Stop Loss Value")
    square_off_value: float = Field(default=None, description="Square Off Value")
    last_traded_price: float = Field(default=None, description="Last Traded Price")
    trailing_stop_loss: str = Field(default=None, description="Trailing Stop Loss")
    trailing_sl_value: float = Field(default=None, description="Trailing SL Value")


class RiskConfig(BaseModel):
    """
    The Constitution: Rules that cannot be broken.
    """

    max_daily_loss: float = Field(..., description="Stop trading if loss hits this amount")
    max_concurrent_trades: int = Field(3, description="Max concurrent positions")

    # Advanced Guardrails
    kill_switch_active: bool = False
    risk_per_trade_pct: float = Field(0.01, description="Risk 1% of capital per trade")
