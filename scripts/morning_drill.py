import asyncio
import sys
import os
import csv
import io
import logging
import httpx
from datetime import datetime
from sqlalchemy import text

# Add project root to path
sys.path.append(os.getcwd())

from app.core.settings import settings
from app.db.session import engine
from app.adapters.kotak.client import kotak_client
from app.models.market_data import InstrumentMaster

from app.core.logger import setup_logging

# Initialize the logger (Colored/JSON)
setup_logging()
logger = logging.getLogger("MorningDrill")

async def run_morning_drill():
    """
    The Morning Drill:
    1. Login to Kotak Neo (Headless).
    2. Download the daily 'Scrip Master' CSV.
    3. Parse and Bulk Insert into Postgres.
    """
    logger.info("🌅 Starting Morning Drill...")

    # Step 1: Login
    try:
        kotak_client.login()
    except Exception as e:
        logger.error(f"❌ Login Failed: {e}")
        return

    # Step 2: Get Scrip Master URL
    try:
        logger.info("🌍 Fetching Scrip Master URL from Kotak...")
        csv_url = kotak_client.get_scrip_master(segment=settings.EXCHANGE_NSE)
        
        if not csv_url or "https" not in str(csv_url):
            raise Exception(f"Invalid URL received: {csv_url}")
            
        logger.info(f"⬇️ Downloading CSV from: {csv_url}")
    except Exception as e:
        logger.error(f"❌ Failed to get Scrip Master URL: {e}")
        return

    # Step 3: Download CSV
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(csv_url, timeout=60.0)
            resp.raise_for_status()
            csv_content = resp.text
    except Exception as e:
        logger.error(f"❌ Failed to download CSV: {e}")
        return

    # Step 4: Parse Data
    logger.info("🔄 Parsing CSV Data...")
    
    # Fix: Clean headers (remove whitespace like 'dTickSize ')
    f = io.StringIO(csv_content)
    
    # Read headers first to clean them
    reader_base = csv.reader(f)
    headers = next(reader_base)
    cleaned_headers = [h.strip() for h in headers]
    
    # Create DictReader with cleaned headers
    reader = csv.DictReader(f, fieldnames=cleaned_headers)
    
    batch_data = []
    
    # Counters for debug
    count_processed = 0
    count_skipped = 0

    for row in reader:
        try:
            # Filter: Only NSE Equity/Derivatives
            segment = row.get('pExchSeg') or row.get('pSegment')
            if segment != settings.EXCHANGE_NSE: # e.g. 'nse_cm'
                continue

            # MAPPING: Live CSV headers -> DB Columns
            # pSymbol -> instrument_token (e.g. 2885)
            # pTrdSymbol -> trading_symbol (e.g. RELIANCE-EQ)
            
            token = row.get('pSymbol') or row.get('pScripCode')
            symbol = row.get('pTrdSymbol') or row.get('pSymbol')
            name = row.get('pDesc') or row.get('pSymbolName')
            
            if not token or not symbol:
                count_skipped += 1
                continue

            record = {
                "instrument_token": int(token),
                "exchange_token": row.get('pScripRefKey') or symbol, # Sometimes useful
                "trading_symbol": symbol,
                "name": name,
                "lot_size": int(row.get('lLotSize', 1)),
                "tick_size": float(row.get('dTickSize', 0.05)),
                "segment": segment,
                "freeze_qty": int(row.get('lFreezeQty', 0)) if row.get('lFreezeQty') else None,
                "updated_at": datetime.now()
            }
            batch_data.append(record)
            count_processed += 1
            
        except (ValueError, KeyError) as e:
            # Skip malformed rows silently to speed up
            count_skipped += 1
            continue

    if not batch_data:
        logger.warning(f"⚠️ No records parsed! Skipped: {count_skipped}. Headers found: {cleaned_headers}")
        return

    # Step 5: Bulk Insert
    logger.info(f"💾 Inserting {len(batch_data)} records into Database...")
    
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE instrument_master CASCADE;"))
            
            # Using Core Insert for max speed
            await conn.execute(
                InstrumentMaster.__table__.insert(),
                batch_data
            )
        logger.info(f"✅ Morning Drill Success! Loaded {len(batch_data)} Instruments.")
        
    except Exception as e:
        logger.error(f"❌ Database Write Failed: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_morning_drill())