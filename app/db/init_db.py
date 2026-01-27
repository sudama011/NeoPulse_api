from app.db.session import engine
from app.models.base import Base

# Import all models so Base knows about them
from app.models.config import SystemConfig
from app.models.market_data import InstrumentMaster
from app.models.orders import OrderLedger
from app.models.users import User


async def init_db():
    try:
        async with engine.begin() as conn:
            print("⏳ Dropping existing tables (optional, strictly for dev)...")
            await conn.run_sync(Base.metadata.drop_all)  # Uncomment for fresh start

            print("⏳ Creating new tables...")
            await conn.run_sync(Base.metadata.create_all)

        print("✅ Database initialized successfully!")
    except Exception as e:
        print(f"❌ Error initializing DB: {e}")
    finally:
        await engine.dispose()
