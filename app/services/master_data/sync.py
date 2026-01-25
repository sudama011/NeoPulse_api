# app/modules/ingestion/drill.py
import csv
import httpx
import logging
import io
from datetime import datetime
from sqlalchemy import text
from app.db.session import engine
from app.adapters.neo_client import neo_client
from app.core.settings import settings

logger = logging.getLogger(__name__)

async def run_morning_drill():
    # 1. Ensure we are logged in
    neo_client.login()
    
    # 2. Get the Scrip Master URL from SDK
    logger.info("🌍 Fetching Scrip Master URL...")
    
    # The SDK returns a URL string for the CSV file
    # exchange_segment="nse_cm" (Equity) or "nse_fo" (Derivatives)
    csv_url_cm = neo_client.client.scrip_master(exchange_segment=settings.EXCHANGE_NSE)
    
    # Check if SDK returned an error dict or a URL string
    if isinstance(csv_url_cm, dict) and "Error" in csv_url_cm:
        raise Exception(f"Failed to get Master URL: {csv_url_cm}")
        
    logger.info(f"⬇️ Downloading CSV from: {csv_url_cm}")

    # 3. Download the actual CSV content
    async with httpx.AsyncClient() as http:
        resp = await http.get(csv_url_cm)
        resp.raise_for_status()
        csv_content = resp.text

    # 4. Parse & Insert (Same logic as before)
    logger.info("🔄 Parsing CSV Data...")
    f = io.StringIO(csv_content)
    reader = csv.DictReader(f)
    
    batch_data = []
    
    # Note: Kotak CSV column names might differ slightly in the new API
    # Usually: pSymbol, pTrdSymbol, pScripCode
    for row in reader:
        try:
            record = {
                "instrument_token": int(row['pScripCode']),
                "exchange_token": row['pSymbol'],
                "trading_symbol": row['pTrdSymbol'],
                "name": row.get('pSymbolName', row['pTrdSymbol']),
                "lot_size": int(row.get('pLotSize', 1)),
                "tick_size": float(row.get('pTickSize', 0.05)),
                "segment": row.get('pExch', 'NSE'),
                "updated_at": datetime.now()
            }
            batch_data.append(record)
        except (ValueError, KeyError):
            continue

    # ... (Bulk Insert Code remains the same) ...
    if batch_data:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE instrument_master CASCADE;"))
            from app.models.market_data import InstrumentMaster
            await conn.execute(InstrumentMaster.__table__.insert(), batch_data)
            
    logger.info(f"✅ Successfully inserted {len(batch_data)} symbols.")