from sqlalchemy import Column, Date, DateTime, Float, Integer, String
from sqlalchemy.sql import func

from app.models.base import Base


class InstrumentMaster(Base):
    __tablename__ = "instrument_master"

    # --- PRIMARY IDENTIFIERS ---
    # pSymbol (1333) -> The Token used for placing orders
    token = Column(Integer, primary_key=True)

    # pTrdSymbol (HDFCBANK-EQ) -> The symbol shown in charts/UI
    trading_symbol = Column(String(50), index=True, nullable=False)

    # pSymbolName (HDFCBANK) -> The symbol used for searching
    symbol = Column(String(50), index=True, nullable=False)

    # pDesc (HDFC BANK LTD) -> Full Name
    name = Column(String(100))

    # pISIN (INE040A01034) -> Unique ID for Equities
    isin = Column(String(20), index=True, nullable=True)

    # --- SEGMENT & TYPE ---
    # pExchange (NSE)
    exchange = Column(String(10), default="NSE")

    # pExchSeg (nse_cm / nse_fo)
    segment = Column(String(10))

    # pGroup (EQ, BE, etc.)
    series = Column(String(10), nullable=True)

    # pInstType (OPTIDX, FUTSTK, etc.)
    instrument_type = Column(String(20), nullable=True)

    # pOptionType (CE/PE)
    option_type = Column(String(5), nullable=True)

    # --- TRADING SPECS ---
    # lLotSize (1)
    lot_size = Column(Integer, default=1)

    # dTickSize (5) / 10^Precision -> 0.05
    tick_size = Column(Float, default=0.05)

    # lFreezeQty (107692) -> Max quantity per order allowed by exchange
    freeze_qty = Column(Integer, nullable=True)

    # --- PRICE BANDS (CIRCUIT LIMITS) ---
    # dHighPriceRange (100770) -> 1007.70
    upper_band = Column(Float, nullable=True)

    # dLowPriceRange (82450) -> 824.50
    lower_band = Column(Float, nullable=True)

    # --- DERIVATIVES INFO ---
    # lExpiryDate (Epoch or Int) -> Converted to Date object
    expiry_date = Column(Date, nullable=True)

    # dStrikePrice (for Options) -> Converted to Float
    strike_price = Column(Float, default=0.0)

    # --- METADATA ---
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Instrument {self.trading_symbol} ({self.token})>"
