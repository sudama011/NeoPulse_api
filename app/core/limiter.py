import asyncio
import functools
import logging
import time

logger = logging.getLogger("RateLimiter")


class RateLimiter:
    """
    Token Bucket Rate Limiter with 'Debt' logic for high concurrency.

    Prevents holding the lock during sleep, allowing multiple requests
    to 'queue' their wait times in parallel.
    """

    def __init__(self, calls_per_second: int = 5, burst_size: int = 10):
        self.rate = calls_per_second
        self.capacity = burst_size
        self.tokens = burst_size
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """
        Attempts to acquire a token. If none available, sleeps until one is ready.
        """
        wait_time = 0.0

        async with self.lock:
            now = time.monotonic()

            # 1. Refill Tokens
            elapsed = now - self.last_refill
            new_tokens = elapsed * self.rate

            if new_tokens > 0:
                self.tokens = min(self.capacity, self.tokens + new_tokens)
                self.last_refill = now

            # 2. Consume Token (or go into debt)
            # We strictly consume 1.0 token per call
            self.tokens -= 1.0

            # 3. Calculate Debt
            if self.tokens < 0:
                # We are in debt. Calculate how long to repay it.
                # tokens is negative, so abs(tokens) is the deficit.
                deficit = abs(self.tokens)
                wait_time = deficit / self.rate

                # Update last_refill to future to prevent 'double dipping'
                # Effectively, we are borrowing against future time.

        # 4. Sleep OUTSIDE the lock (Non-blocking wait)
        if wait_time > 0:
            logger.debug(f"⏳ Rate Limit: Sleeping {wait_time:.3f}s")
            await asyncio.sleep(wait_time)

    def limit(self, func):
        """Decorator to rate limit a function."""

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            await self.acquire()
            return await func(*args, **kwargs)

        return wrapper

    async def __aenter__(self):
        """Context Manager support."""
        await self.acquire()

    async def __aexit__(self, exc_type, exc, tb):
        pass


# --- Global Instances ---

# Kotak API Limit: Usually 10-20 req/sec depending on plan
# We set strict 5/sec to stay safe, with a burst of 10 for rapid updates.
kotak_limiter = RateLimiter(calls_per_second=5, burst_size=10)

# F&O Order Limiter: Exchanges have strict rules per second
order_limiter = RateLimiter(calls_per_second=5, burst_size=10)
