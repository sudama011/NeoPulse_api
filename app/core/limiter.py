import time
import asyncio
import logging

logger = logging.getLogger("RateLimiter")

class TokenBucket:
    def __init__(self, rate_per_second: int = 10, capacity: int = 20):
        self.capacity = capacity
        self.tokens = capacity
        self.rate = rate_per_second
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            # 1. Refill Tokens based on time passed
            elapsed = now - self.last_refill
            new_tokens = elapsed * self.rate
            
            if new_tokens > 1:
                self.tokens = min(self.capacity, self.tokens + new_tokens)
                self.last_refill = now
            
            # 2. Consume Token
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            else:
                # Calculate wait time
                wait_time = (1 - self.tokens) / self.rate
                logger.warning(f"⏳ Rate Limit Hit! Sleeping {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                
                # Recursive retry after sleep
                self.tokens = 0 # Reset slightly
                self.last_refill = time.monotonic()
                return True

# Global Limiter (10 req/sec, burst of 20)
api_limiter = TokenBucket(rate_per_second=5, capacity=10)