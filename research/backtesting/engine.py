import sys
import os
import yfinance as yf
import pandas as pd
import logging
import asyncio
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

from app.core.logger import setup_logging
from app.services.strategy.lib.momentum import MomentumStrategy
from app.adapters.telegram.client import telegram_client 

setup_logging()
logger = logging.getLogger("Backtester")

class BacktestService:
    def __init__(self, symbol: str, days: int = 5):
        self.symbol = symbol
        self.days = days
        self.strategy = MomentumStrategy(symbol=symbol, token="BACKTEST")
        self.initial_capital = 100000.0
        self.balance = self.initial_capital
        
        # Override Strategy's "Execute" method
        self.strategy.execute_trade = self.mock_execution

        self.trades = []
        self.current_qty = 0 
        self.entry_price = 0.0

    async def mock_execution(self, side, price):
        qty = 25 
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

    async def run(self):
        logger.info(f"📥 Downloading data for {self.symbol} ({self.days} days)...")
        df = yf.download(tickers=self.symbol, period=f"{self.days}d", interval="1m", progress=False)
        
        if df.empty:
            logger.error("❌ No data found!")
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