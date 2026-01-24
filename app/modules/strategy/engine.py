import asyncio
import logging
from app.core.events import event_bus

logger = logging.getLogger("StrategyEngine")

class StrategyManager:
    """
    The Central Dispatcher.
    Routes incoming Ticks -> Correct Strategy Instance.
    """
    _instance = None

    def __init__(self):
        self.strategies = {} # Map: token_id -> StrategyObject
        self.is_running = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = StrategyManager()
        return cls._instance

    def add_strategy(self, strategy_class, symbol: str, token: str):
        """
        Registers a new strategy for a specific stock.
        """
        token = str(token)
        
        # Avoid duplicate strategies for same token
        if token in self.strategies:
            logger.warning(f"⚠️ Strategy already exists for {symbol} ({token})")
            return

        # Instantiate the strategy (e.g., MomentumStrategy(symbol, token))
        strategy = strategy_class(symbol=symbol, token=token)
        self.strategies[token] = strategy
        logger.info(f"✅ Strategy Registered: {strategy.name} for {symbol}")

    async def start(self):
        """
        The Main Loop: Consumes ticks from EventBus and feeds them to Strategies.
        """
        logger.info("🚀 Strategy Engine Started. Waiting for ticks...")
        self.is_running = True

        while self.is_running:
            try:
                # 1. Get Tick from Queue (Non-blocking wait)
                tick_payload = await event_bus.tick_queue.get()
                
                # 2. Parse (Handle both list and dict formats from SDK)
                ticks = []
                if isinstance(tick_payload, dict) and 'data' in tick_payload:
                    ticks = tick_payload['data']
                elif isinstance(tick_payload, list):
                    ticks = tick_payload
                
                # 3. Route to specific Strategy
                for tick in ticks:
                    # 'tk' is the Token ID in Kotak SDK
                    token = str(tick.get('tk'))
                    
                    if token in self.strategies:
                        # ⚡ Fire and Forget! 
                        # We use create_task so one slow strategy doesn't block the next tick.
                        asyncio.create_task(
                            self.strategies[token].on_tick(tick)
                        )
                        
            except Exception as e:
                logger.error(f"🔥 Engine Error: {e}")

# Global Singleton
strategy_engine = StrategyManager.get_instance()