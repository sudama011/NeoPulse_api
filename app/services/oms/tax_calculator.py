class TaxCalculator:
    """
    Estimates charges for NSE Equity Intraday.
    """

    def calculate(self, buy_val: float, sell_val: float, qty: int):
        turnover = buy_val + sell_val

        # 1. Brokerage Kotak Neo API Intraday 0 fee
        brokerage = 0.0

        # 2. STT (0.025% on Sell side for Intraday Equity)
        stt = sell_val * 0.00025

        # 3. Exchange Txn Charge (NSE: 0.00325%)
        txn_charge = turnover * 0.0000325

        # 4. GST (18% on Brokerage + Txn Charge)
        gst = (brokerage + txn_charge) * 0.18

        # 5. SEBI Charges (0.0001% of turnover)
        sebi = turnover * 0.000001

        # 6. Stamp Duty (0.003% on Buy side)
        stamp = buy_val * 0.00003

        total_tax = brokerage + stt + txn_charge + gst + sebi + stamp
        return round(total_tax, 2)
