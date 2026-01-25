import logging
import asyncio
import pandas as pd
import yfinance as yf
from datetime import datetime
from typing import List, Dict, Type

# Import your actual strategies
from app.services.strategy.base import BaseStrategy

# Import Risk Management (Logic only)
from app.services.risk.position_sizer import CapitalManager

logger = logging.getLogger("BacktestEngine")

class BacktestEngine:
    def __init__(self, 
                 symbol: str, 
                 strategy_class: Type[BaseStrategy], 
                 days: int = 5, 
                 capital: float = 100000.0,
                 params: Dict = None):
        
        self.symbol = symbol
        self.days = days
        self.initial_capital = capital
        self.balance = capital
        self.equity_curve = [capital]
        
        # Initialize Strategy
        # We pass a dummy token since we are backtesting
        self.strategy = strategy_class(symbol=symbol, token="BACKTEST")
        
        # Apply custom params if provided
        if params:
            for k, v in params.items():
                if hasattr(self.strategy, k):
                    setattr(self.strategy, k, v)

        # Initialize Capital Manager (Use real logic)
        self.capital_manager = CapitalManager(total_capital=capital, risk_per_trade_pct=0.01)
        self.strategy.capital_manager = self.capital_manager

        # Override Strategy Execution for Simulation
        self.strategy.execute_trade = self.mock_execution
        
        # Tracking
        self.trades: List[Dict] = []
        self.current_qty = 0
        self.entry_price = 0.0
        self.max_drawdown = 0.0

    async def mock_execution(self, side: str, price: float):
        """
        Intercepts strategy 'execute_trade' calls.
        Simulates fills and tracks PnL.
        """
        # --- FIX START: Handle NoneType for current_candle ---
        # If trade is triggered on candle close, current_candle is None.
        # We must fallback to the last closed candle (candles[-1]).
        if self.strategy.current_candle:
            timestamp = self.strategy.current_candle.get('start_time')
        elif self.strategy.candles:
            timestamp = self.strategy.candles[-1].get('start_time')
        else:
            timestamp = datetime.now()
        # --- FIX END ---

        # 1. Calculate Position Size (Dynamic)
        if self.current_qty == 0:
            # Entry Logic
            try:
                # Assuming strategy has stop_loss_pct defined
                sl_pct = getattr(self.strategy, 'stop_loss_pct', 0.01)
                
                # Calculate SL price based on direction
                if side == "BUY":
                    stop_loss_price = price * (1 - sl_pct)
                else:
                    stop_loss_price = price * (1 + sl_pct)
                    
                qty = self.capital_manager.calculate_quantity(price, stop_loss_price)
                
            except Exception as e:
                logger.error(f"Sizing error: {e}")
                qty = 10  # Fallback

            if qty < 1: 
                return # Skip trade if risk logic says 0 qty

            self.current_qty = qty if side == "BUY" else -qty
            self.entry_price = price
            
            # Sync internal strategy state
            self.strategy.position = self.current_qty
            self.strategy.entry_price = price
            
            logger.info(f"🔵 [OPEN] {side} {qty} @ {price:.2f} | {timestamp}")

        # 2. Close Logic
        else:
            # Calculate PnL
            if self.current_qty > 0: # Long Close
                pnl = (price - self.entry_price) * abs(self.current_qty)
                direction = "LONG"
            else: # Short Close
                pnl = (self.entry_price - price) * abs(self.current_qty)
                direction = "SHORT"

            self.balance += pnl
            self.equity_curve.append(self.balance)
            
            # Record Trade
            self.trades.append({
                "entry_time": timestamp, # Approximate (exit time)
                "type": direction,
                "entry": self.entry_price,
                "exit": price,
                "qty": abs(self.current_qty),
                "pnl": pnl
            })
            
            logger.info(f"🔴 [CLOSE] {direction} @ {price:.2f} | PnL: {pnl:+.2f}")
            
            # Reset State
            self.current_qty = 0
            self.entry_price = 0.0
            self.strategy.position = 0
            self.strategy.entry_price = 0.0

    async def run(self):
        """Main simulation loop."""
        logger.info(f"📥 Fetching {self.days} days of data for {self.symbol}...")
        try:
            df = yf.download(
                tickers=self.symbol, 
                period=f"{self.days}d", 
                interval="1m", 
                progress=False
            )
            
            # Fix MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            if df.empty:
                logger.error("❌ No data fetched.")
                return
                
        except Exception as e:
            logger.error(f"❌ Data fetch failed: {e}")
            return

        logger.info(f"✅ Loaded {len(df)} candles. Starting simulation...")

        for index, row in df.iterrows():
            ts = index.to_pydatetime()
            open_p = float(row['Open'])
            high_p = float(row['High'])
            low_p  = float(row['Low'])
            close_p= float(row['Close'])
            vol    = int(row['Volume'])

            # ---------------------------------------------------------
            # ✅ IMPROVED CANDLE SIMULATION
            # Green Candle: Open -> Low -> High -> Close
            # Red Candle:   Open -> High -> Low -> Close
            # This prevents unrealistic stop-loss/take-profit hits.
            # ---------------------------------------------------------
            if close_p >= open_p:
                # Green
                tick_sequence = [
                    (open_p, 5),
                    (low_p, 20),
                    (high_p, 40),
                    (close_p, 59)
                ]
            else:
                # Red
                tick_sequence = [
                    (open_p, 5),
                    (high_p, 20),
                    (low_p, 40),
                    (close_p, 59)
                ]

            for price, second in tick_sequence:
                tick = {
                    'ltp': price, 
                    'v': vol/4, 
                    'ts': ts.replace(second=second),
                    'tk': 'BACKTEST'
                }
                await self.strategy.on_tick(tick)
        
        self.generate_report()

    def generate_report(self):
        logger.info("\n" + "="*40)
        logger.info(f"📊 REPORT: {self.strategy.name} on {self.symbol}")
        logger.info("="*40)
        
        total_trades = len(self.trades)
        if total_trades == 0:
            logger.warning("No trades generated.")
            return

        wins = [t for t in self.trades if t['pnl'] > 0]
        losses = [t for t in self.trades if t['pnl'] <= 0]
        
        win_rate = (len(wins) / total_trades) * 100
        net_pnl = self.balance - self.initial_capital
        
        # Calculate Drawdown
        peak = self.initial_capital
        max_dd = 0.0
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        logger.info(f"💰 Final Balance:   ₹{self.balance:,.2f}")
        logger.info(f"📈 Net Profit:      ₹{net_pnl:,.2f} ({(net_pnl/self.initial_capital)*100:.2f}%)")
        logger.info(f"🎲 Total Trades:    {total_trades}")
        logger.info(f"🏆 Win Rate:        {win_rate:.2f}%")
        logger.info(f"📉 Max Drawdown:    {max_dd*100:.2f}%")
        logger.info("="*40 + "\n")