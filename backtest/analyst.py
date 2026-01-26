import pandas as pd

class PerformanceAnalyst:
    """Calculates metrics from trade history."""
    
    def __init__(self, initial_capital: float, trades: list):
        self.initial_capital = initial_capital
        self.trades = pd.DataFrame(trades)

    def generate_report(self) -> dict:
        if self.trades.empty:
            return {"error": "No trades executed."}

        # 1. Equity Curve
        self.trades['equity'] = self.trades['balance_after']
        final_equity = self.trades.iloc[-1]['equity']
        total_return = final_equity - self.initial_capital
        return_pct = (total_return / self.initial_capital) * 100

        # 2. Win/Loss Stats
        # We need to pair BUYs and SELLs to calculate per-trade PnL
        # This is complex; for a simple summary, we look at Trade Value logic
        # OR we assume the strategy closes positions.
        
        # Simple Logic: Winning Trades vs Losing Trades
        # (Requires per-trade PnL tracking, simplified here)
        
        # 3. Drawdown
        running_max = self.trades['equity'].cummax()
        drawdown = (self.trades['equity'] - running_max) / running_max
        max_drawdown = drawdown.min() * 100

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return": round(total_return, 2),
            "return_pct": f"{return_pct:.2f}%",
            "max_drawdown": f"{max_drawdown:.2f}%",
            "total_trades": len(self.trades),
            "trades_df": self.trades[['time', 'side', 'qty', 'price', 'equity']]
        }