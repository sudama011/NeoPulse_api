import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

from app.core.executors import run_blocking

logger = logging.getLogger("CircuitBreaker")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"  # ✅ Healthy
    OPEN = "OPEN"  # ❌ Broken (Fail Fast)
    HALF_OPEN = "HALF_OPEN"  # 🟡 Recovery (Testing)


class CircuitOpenError(Exception):
    """Raised when the circuit is OPEN or probe is in progress."""

    pass


class CircuitBreaker:
    """
    Thread-safe, Async/Sync compatible Circuit Breaker.
    Features:
    - Auto-detects Sync vs Async functions.
    - Offloads Sync functions (Kotak SDK) to thread pool.
    - Strict Half-Open 'Canary' logic (only 1 probe allowed).
    """

    def __init__(
        self, name: str, failure_threshold: int = 5, recovery_timeout: int = 60, expected_exception: type = Exception
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        # State
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._last_error_msg = ""

        # Concurrency Control
        self._probe_in_progress = False  # Track if a canary request is flying
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Executes a function with protection.
        Auto-handles Sync (Thread) vs Async (Await) execution.
        """
        # 1. State Check & Gatekeeping
        async with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if enough time has passed to try recovery
                if self._should_attempt_recovery():
                    # Transition to HALF_OPEN but strictly control the probe
                    self._state = CircuitState.HALF_OPEN
                    self._probe_in_progress = True
                    logger.warning(f"🟡 [{self.name}] Circuit HALF-OPEN. Sending Probe...")
                else:
                    raise CircuitOpenError(
                        f"🛑 [{self.name}] Circuit OPEN. Retry in {self._remaining_recovery_time():.1f}s."
                    )

            elif self._state == CircuitState.HALF_OPEN:
                # If we are already Half-Open, we strictly reject parallel requests
                # while the probe is running.
                raise CircuitOpenError(f"🛑 [{self.name}] Probe in progress. Please wait.")

        # 2. Execution (Outside lock to allow concurrency for CLOSED state)
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                # 🚀 Crucial: Offload blocking Kotak calls to thread
                result = await run_blocking(func, *args, **kwargs)

            # 3. Success Handler
            await self._handle_success()
            return result

        except self.expected_exception as e:
            # 4. Failure Handler
            await self._handle_failure(e)
            raise e

    def _should_attempt_recovery(self) -> bool:
        if not self._last_failure_time:
            return True
        elapsed = (datetime.now() - self._last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def _remaining_recovery_time(self) -> float:
        if not self._last_failure_time:
            return 0.0
        elapsed = (datetime.now() - self._last_failure_time).total_seconds()
        return max(0.0, self.recovery_timeout - elapsed)

    async def _handle_success(self):
        # Optimistic check to avoid locking if already closed
        if self._state == CircuitState.CLOSED:
            return

        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._last_failure_time = None
                self._probe_in_progress = False
                logger.info(f"🟢 [{self.name}] Circuit CLOSED. Service Recovered.")

    async def _handle_failure(self, error: Exception):
        async with self._lock:
            self._last_failure_time = datetime.now()
            self._last_error_msg = str(error)

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed, go back to OPEN immediately
                self._state = CircuitState.OPEN
                self._probe_in_progress = False
                logger.error(f"🔴 [{self.name}] Probe Failed. Circuit Re-OPENED. Error: {error}")

            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.critical(
                        f"💔 [{self.name}] Threshold Reached ({self._failure_count} failures). Circuit OPENED."
                    )


# --- Instances ---
broker_circuit_breaker = CircuitBreaker(
    name="Broker API",
    failure_threshold=3,
    recovery_timeout=30,
)

positions_circuit_breaker = CircuitBreaker(
    name="Positions API",
    failure_threshold=5,
    recovery_timeout=60,
)
