import sys
import os
import yfinance as yf
import pandas as pd
import logging
import asyncio
import time
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

from app.core.logger import setup_logging
from app.services.strategy.lib.momentum import MomentumStrategy
from app.adapters.telegram_client import telegram_client 

setup_logging()
logger = logging.getLogger("Backtester")

class BacktestService:
    def __init__(self, symbol: str, days: int = 5, strategy_class=None, initial_capital: float = 100000.0, custom_dataframe=None):
        self.symbol = symbol
        self.days = days
        self.initial_capital = initial_capital
        self.balance = self.initial_capital
        self.custom_dataframe = custom_dataframe  # For pre-fetched data
        
        # Use provided strategy class or default to Momentum
        if strategy_class is None:
            strategy_class = MomentumStrategy
        
        self.strategy = strategy_class(symbol=symbol, token="BACKTEST")
        
        # Override Strategy's "Execute" method for backtesting
        self.strategy.execute_trade = self.mock_execution

        self.trades = []
        self.current_qty = 0 
        self.entry_price = 0.0

    async def mock_execution(self, side, price):
        """Mock trade execution for backtesting."""
        # Use dynamic position sizing if capital manager is available
        if hasattr(self.strategy, 'capital_manager') and self.strategy.capital_manager:
            entry_price = price
            if side == "BUY":
                stop_loss = entry_price * (1 - self.strategy.stop_loss_pct)
            else:
                stop_loss = entry_price * (1 + self.strategy.stop_loss_pct)
            
            qty = self.strategy.capital_manager.calculate_quantity(entry_price, stop_loss)
            if qty < 1:
                logger.warning(f"⚠️ Position size too small: {qty}")
                return
        else:
            qty = 25  # Fallback
        
        timestamp = self.strategy.current_candle['start_time'] if self.strategy.current_candle else datetime.now()

        # 1. OPEN NEW POSITION
        if self.current_qty == 0:
            self.current_qty = qty if side == "BUY" else -qty
            self.entry_price = price
            
            # Sync Strategy
            self.strategy.position = self.current_qty
            self.strategy.entry_price = price
            
            logger.info(f"🔵 [OPEN] {side} {qty} @ {price:.2f} | Time: {timestamp}")

            # 🔔 2. SEND TELEGRAM ALERT (BACKTEST VERSION)
            msg = (
                f"<b>🧪 BACKTEST TRADE</b>\n"
                f"🔵 <b>OPEN {side}</b> {self.symbol}\n"
                f"💵 Price: {price:.2f}\n"
                f"⏰ Time: {timestamp}"
            )
            # We use 'await' here to ensure it sends before the script keeps running
            await telegram_client.send_alert(msg)

        # 3. CLOSE EXISTING POSITION
        else:
            if self.current_qty > 0: # Closing Long
                pnl = (price - self.entry_price) * abs(self.current_qty)
                direction = "LONG"
            else: # Closing Short
                pnl = (self.entry_price - price) * abs(self.current_qty)
                direction = "SHORT"

            self.balance += pnl
            self.trades.append({
                "time": timestamp,
                "type": direction,
                "entry": self.entry_price,
                "exit": price,
                "pnl": pnl
            })
            
            logger.info(f"🔴 [CLOSE] {direction} @ {price:.2f} | PnL: ₹{pnl:.2f}")
            
            # 🔔 4. SEND CLOSE ALERT
            emoji = "🟢" if pnl > 0 else "🔻"
            msg = (
                f"<b>🧪 BACKTEST CLOSE</b>\n"
                f"🔴 <b>CLOSE {direction}</b>\n"
                f"💵 Price: {price:.2f}\n"
                f"{emoji} PnL: ₹{pnl:.2f}"
            )
            # await telegram_client.send_alert(msg)
            
            # Reset
            self.current_qty = 0
            self.entry_price = 0.0
            self.strategy.position = 0
            self.strategy.entry_price = 0.0

    async def run(self, use_provided_data: bool = False):
        """
        Run backtest with real yfinance data or provided dataframe.
        
        Args:
            use_provided_data: If True, uses self.custom_dataframe instead of downloading
        """
        # Get data source
        if use_provided_data and self.custom_dataframe is not None:
            df = self.custom_dataframe
            logger.info(f"📊 Using provided data with {len(df)} candles")
        else:
            logger.info(f"📥 Downloading real market data for {self.symbol} ({self.days} days)...")
            
            # Retry logic with exponential backoff
            max_retries = 5
            retry_delay = 3  # seconds
            df = None
            
            for attempt in range(max_retries):
                try:
                    df = yf.download(
                        tickers=self.symbol, 
                        period=f"{self.days}d", 
                        interval="1m", 
                        progress=False,
                        timeout=30
                    )
                    
                    if df is not None and not df.empty:
                        logger.info(f"✅ Successfully downloaded {len(df)} candles on attempt {attempt + 1}")
                        break
                    else:
                        # Empty dataframe - treat as retriable error
                        if attempt < max_retries - 1:
                            logger.warning(f"⚠️ Download attempt {attempt + 1}: Empty data received")
                            logger.info(f"🔄 Retrying in {retry_delay} seconds...")
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                        else:
                            logger.error("❌ No data found after all retries!")
                            logger.error("💡 Possible solutions:")
                            logger.error("  1. yfinance is rate-limited - wait 5-10 minutes and try again")
                            logger.error("  2. Check if symbol is correct: RELIANCE.NS")
                            logger.error("  3. Use NSE Indian stock codes")
                            return
                    
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"⚠️ Download attempt {attempt + 1} failed: {str(e)[:80]}"
                        )
                        logger.info(f"🔄 Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        logger.error(
                            f"❌ Failed to download data after {max_retries} attempts"
                        )
                        logger.error(f"Last error: {e}")
                        logger.error("💡 Suggestions:")
                        logger.error("  1. Check your internet connection")
                        logger.error("  2. Wait a few minutes (yfinance rate limiting)")
                        logger.error("  3. Try a different symbol or data source")
                        return
            
            if df is None or df.empty:
                logger.error("❌ Failed to retrieve data")
                return

        # Flatten Columns Fix
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        logger.info(f"✅ Loaded {len(df)} candles. Starting simulation...")

        for index, row in df.iterrows():
            ts = index.to_pydatetime()
            
            try:
                open_p = float(row['Open'])
                high_p = float(row['High'])
                low_p  = float(row['Low'])
                close_p= float(row['Close'])
                vol    = int(row['Volume'])
            except Exception:
                open_p = float(row['Open'].iloc[0])
                high_p = float(row['High'].iloc[0])
                low_p  = float(row['Low'].iloc[0])
                close_p= float(row['Close'].iloc[0])
                vol    = int(row['Volume'].iloc[0])

            ticks = [
                {'ltp': open_p, 'v': vol/4, 'ts': ts.replace(second=5)},
                {'ltp': low_p,  'v': vol/4, 'ts': ts.replace(second=20)},
                {'ltp': high_p, 'v': vol/4, 'ts': ts.replace(second=40)},
                {'ltp': close_p,'v': vol/4, 'ts': ts.replace(second=59)},
            ]

            for tick in ticks:
                tick['tk'] = 'BACKTEST'
                await self.strategy.on_tick(tick)

        self.generate_report()

    def generate_report(self):
        # (Same as before)
        logger.info("="*40)
        logger.info("📊 BACKTEST REPORT")
        logger.info("="*40)
        
        total_trades = len(self.trades)
        if total_trades == 0:
            logger.warning("No trades triggered.")
            return

        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
        total_pnl = self.balance - self.initial_capital
        
        logger.info(f"💰 Final Capital:   ₹{self.balance:,.2f}")
        logger.info(f"📈 Net Profit:      ₹{total_pnl:,.2f}")
        logger.info(f"🎲 Total Trades:    {total_trades}")
        logger.info(f"🏆 Win Rate:        {win_rate:.1f}%")
        logger.info("="*40)