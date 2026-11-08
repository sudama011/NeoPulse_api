"""
Statutory charges for NSE equity trades.

Kotak Neo charges zero brokerage on Trade API orders, but statutory levies still apply.
Rates are kept in one frozen dataclass so they can be updated in one place.
"""

from dataclasses import dataclass

from app.domain.models import ProductType, Side


@dataclass(frozen=True)
class ChargeRates:
    brokerage: float = 0.0  # Kotak Neo Trade API: zero brokerage
    exchange_txn: float = 0.0000297  # NSE transaction charge (both sides)
    sebi: float = 0.000001  # Rs 10 per crore (both sides)
    gst: float = 0.18  # on brokerage + exchange + SEBI
    # Intraday (MIS)
    stt_intraday_sell: float = 0.00025
    stamp_intraday_buy: float = 0.00003
    # Delivery (CNC)
    stt_delivery: float = 0.001  # both sides
    stamp_delivery_buy: float = 0.00015


class ChargesCalculator:
    def __init__(self, rates: ChargeRates = ChargeRates()):
        self.rates = rates

    def compute(self, side: Side, qty: int, price: float, product: ProductType | str = ProductType.MIS) -> float:
        turnover = qty * price
        if turnover <= 0:
            return 0.0
        r = self.rates
        delivery = ProductType(product) == ProductType.CNC

        if delivery:
            stt = turnover * r.stt_delivery
            stamp = turnover * r.stamp_delivery_buy if side is Side.BUY else 0.0
        else:
            stt = turnover * r.stt_intraday_sell if side is Side.SELL else 0.0
            stamp = turnover * r.stamp_intraday_buy if side is Side.BUY else 0.0

        brokerage = turnover * r.brokerage
        exchange = turnover * r.exchange_txn
        sebi = turnover * r.sebi
        gst = (brokerage + exchange + sebi) * r.gst
        return round(brokerage + stt + exchange + sebi + gst + stamp, 4)
