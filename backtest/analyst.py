import logging
from typing import Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger("BacktestAnalyst")


class PerformanceAnalyst:
    """
    Comprehensive performance analytics for backtest results.

    Calculates:
    - Equity Curve & Returns
    - Win/Loss Rate, Profit Factor
    - Sharpe Ratio, Sortino Ratio
    - Max Drawdown (Value & Duration)
    - Average Win / Average Loss
    - Expectancy
    """

    TRADING_DAYS_PER_YEAR = 252
    RISK_FREE_RATE = 0.06  # 6% for India (adjust as needed)

    def __init__(self, initial_capital: float, trades: List[Dict]):
        self.initial_capital = initial_capital
        self.raw_trades = trades
        self.trades = pd.DataFrame(trades) if trades else pd.DataFrame()

    def _pair_trades(self) -> pd.DataFrame:
        """
        Pairs BUY/SELL trades into round-trips to calculate per-trade P&L.
        """
        if self.trades.empty:
            return pd.DataFrame()

        paired = []
        open_trades = []  # Stack of open entries

        for _, row in self.trades.iterrows():
            if row["side"] == "BUY":
                open_trades.append(row)
            elif row["side"] == "SELL" and open_trades:
                entry = open_trades.pop(0)  # FIFO
                pnl = (row["price"] - entry["price"]) * entry["qty"]
                pnl_pct = ((row["price"] - entry["price"]) / entry["price"]) * 100

                paired.append({
                    "entry_time": entry["time"],
                    "exit_time": row["time"],
                    "entry_price": entry["price"],
                    "exit_price": row["price"],
                    "qty": entry["qty"],
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                })

        return pd.DataFrame(paired) if paired else pd.DataFrame()

    def generate_report(self) -> dict:
        """Generate a comprehensive performance report."""
        if self.trades.empty:
            return {"error": "No trades executed.", "total_trades": 0}

        # --- 1. Equity Curve ---
        self.trades["equity"] = self.trades["balance_after"]
        final_equity = float(self.trades.iloc[-1]["equity"])
        total_return = final_equity - self.initial_capital
        return_pct = (total_return / self.initial_capital) * 100

        # --- 2. Pair Trades for P&L ---
        paired = self._pair_trades()
        round_trips = len(paired)

        # Win/Loss stats
        wins = paired[paired["pnl"] > 0] if not paired.empty else pd.DataFrame()
        losses = paired[paired["pnl"] < 0] if not paired.empty else pd.DataFrame()

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / round_trips * 100) if round_trips > 0 else 0.0

        avg_win = float(wins["pnl"].mean()) if not wins.empty else 0.0
        avg_loss = float(losses["pnl"].mean()) if not losses.empty else 0.0
        largest_win = float(wins["pnl"].max()) if not wins.empty else 0.0
        largest_loss = float(losses["pnl"].min()) if not losses.empty else 0.0

        # Profit Factor = Gross Profit / Gross Loss
        gross_profit = float(wins["pnl"].sum()) if not wins.empty else 0.0
        gross_loss = abs(float(losses["pnl"].sum())) if not losses.empty else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        # Expectancy = (Win% × AvgWin) - (Loss% × |AvgLoss|)
        expectancy = 0.0
        if round_trips > 0:
            expectancy = (win_count / round_trips * avg_win) + (loss_count / round_trips * avg_loss)

        # --- 3. Drawdown ---
        equity_series = self.trades["equity"].astype(float)
        running_max = equity_series.cummax()
        drawdown = (equity_series - running_max) / running_max
        max_drawdown_pct = float(drawdown.min()) * 100
        max_drawdown_value = float((equity_series - running_max).min())

        # --- 4. Risk-Adjusted Returns ---
        # Daily returns from equity curve
        daily_equity = equity_series.resample("D").last().dropna() if hasattr(equity_series.index, "freq") else equity_series
        daily_returns = daily_equity.pct_change().dropna()

        sharpe_ratio = self._sharpe(daily_returns)
        sortino_ratio = self._sortino(daily_returns)

        # --- 5. Build Report ---
        report = {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return": round(total_return, 2),
            "return_pct": round(return_pct, 2),
            # Trade Stats
            "total_orders": len(self.trades),
            "round_trips": round_trips,
            "wins": win_count,
            "losses": loss_count,
            "win_rate": round(win_rate, 2),
            # P&L Stats
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "∞",
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),
            "expectancy": round(expectancy, 2),
            # Risk Metrics
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "max_drawdown_value": round(max_drawdown_value, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "sortino_ratio": round(sortino_ratio, 2),
        }

        # Attach DataFrames for plotting
        report["_equity_curve"] = equity_series
        report["_paired_trades"] = paired

        return report

    def _sharpe(self, returns: pd.Series) -> float:
        """Annualized Sharpe Ratio."""
        if returns.empty or returns.std() == 0:
            return 0.0
        excess = returns.mean() - (self.RISK_FREE_RATE / self.TRADING_DAYS_PER_YEAR)
        return float(excess / returns.std() * np.sqrt(self.TRADING_DAYS_PER_YEAR))

    def _sortino(self, returns: pd.Series) -> float:
        """Annualized Sortino Ratio (penalizes downside volatility only)."""
        if returns.empty:
            return 0.0
        downside = returns[returns < 0]
        if downside.empty or downside.std() == 0:
            return 0.0
        excess = returns.mean() - (self.RISK_FREE_RATE / self.TRADING_DAYS_PER_YEAR)
        return float(excess / downside.std() * np.sqrt(self.TRADING_DAYS_PER_YEAR))

    def print_report(self, report: dict):
        """Pretty-print the report to console."""
        if "error" in report:
            print(f"\n❌ {report['error']}")
            return

        print("\n" + "=" * 55)
        print("📊  BACKTEST PERFORMANCE REPORT")
        print("=" * 55)
        print(f"  💰 Initial Capital:    ₹{report['initial_capital']:>12,.2f}")
        print(f"  💰 Final Equity:       ₹{report['final_equity']:>12,.2f}")
        print(f"  📈 Total Return:       ₹{report['total_return']:>12,.2f}  ({report['return_pct']:.2f}%)")
        print("-" * 55)
        print(f"  🔢 Total Orders:       {report['total_orders']:>8}")
        print(f"  🔄 Round Trips:        {report['round_trips']:>8}")
        print(f"  ✅ Wins:               {report['wins']:>8}")
        print(f"  ❌ Losses:             {report['losses']:>8}")
        print(f"  🎯 Win Rate:           {report['win_rate']:>7.1f}%")
        print("-" * 55)
        print(f"  📈 Gross Profit:       ₹{report['gross_profit']:>12,.2f}")
        print(f"  📉 Gross Loss:         ₹{report['gross_loss']:>12,.2f}")
        print(f"  ⚖️  Profit Factor:      {report['profit_factor']:>8}")
        print(f"  🎲 Expectancy:         ₹{report['expectancy']:>12,.2f}")
        print(f"  🏆 Avg Win:            ₹{report['avg_win']:>12,.2f}")
        print(f"  💀 Avg Loss:           ₹{report['avg_loss']:>12,.2f}")
        print(f"  🚀 Largest Win:        ₹{report['largest_win']:>12,.2f}")
        print(f"  🔻 Largest Loss:       ₹{report['largest_loss']:>12,.2f}")
        print("-" * 55)
        print(f"  📉 Max Drawdown:       {report['max_drawdown_pct']:>7.2f}%  (₹{report['max_drawdown_value']:,.2f})")
        print(f"  📐 Sharpe Ratio:       {report['sharpe_ratio']:>8.2f}")
        print(f"  📐 Sortino Ratio:      {report['sortino_ratio']:>8.2f}")
        print("=" * 55)
