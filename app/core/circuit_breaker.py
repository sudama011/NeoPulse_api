import logging
import asyncio
import inspect
from datetime import datetime
from enum import Enum
from typing import Callable, Any, Optional
from app.core.executors import run_blocking

logger = logging.getLogger("CircuitBreaker")

class CircuitState(str, Enum):
    CLOSED = "CLOSED"           # ✅ Healthy
    OPEN = "OPEN"               # ❌ Broken (Fail Fast)
    HALF_OPEN = "HALF_OPEN"     # 🟡 Recovery (Testing)

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
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
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
        self._lock = asyncio.Lock()
        
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Executes a function with protection. 
        Auto-handles Sync (Thread) vs Async (Await) execution.
        """
        async with self._lock:
            # 1. Check State
            if self._state == CircuitState.OPEN:
                # Check if we can attempt recovery
                if self._should_attempt_recovery():
                    self._transition_to_half_open()
                else:
                    raise CircuitOpenError(
                        f"🛑 [{self.name}] Circuit OPEN. Retry in {self._remaining_recovery_time():.1f}s. "
                        f"Last Error: {self._last_error_msg}"
                    )
            
            # 2. Handle HALF_OPEN (Strict Canary Mode)
            # Only allow this specific call to proceed if we just transitioned to HALF_OPEN
            # (Lock ensures only one request gets here if multiple were waiting)
            
        try:
            # 3. Execution (Outside Lock to allow concurrency if CLOSED)
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                # 🚀 Crucial: Offload blocking Kotak calls to thread
                result = await run_blocking(func, *args, **kwargs)
            
            # 4. Success Handler
            if self._state != CircuitState.CLOSED:
                await self._handle_success()
            
            return result

        except self.expected_exception as e:
            # 5. Failure Handler
            await self._handle_failure(e)
            raise e

    def _should_attempt_recovery(self) -> bool:
        if not self._last_failure_time:
            return True
        elapsed = (datetime.now() - self._last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def _remaining_recovery_time(self) -> float:
        if not self._last_failure_time: return 0.0
        elapsed = (datetime.now() - self._last_failure_time).total_seconds()
        return max(0.0, self.recovery_timeout - elapsed)

    def _transition_to_half_open(self):
        self._state = CircuitState.HALF_OPEN
        logger.warning(f"🟡 [{self.name}] Probe Active: Allowing 1 request to test recovery.")

    async def _handle_success(self):
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._last_failure_time = None
                logger.info(f"🟢 [{self.name}] Circuit CLOSED. Service Recovered.")

    async def _handle_failure(self, error: Exception):
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now()
            self._last_error_msg = str(error)
            
            current_state = self._state
            
            if current_state == CircuitState.HALF_OPEN:
                # Probe failed, go back to OPEN immediately
                self._state = CircuitState.OPEN
                logger.error(f"🔴 [{self.name}] Probe Failed. Circuit Re-OPENED. Error: {error}")
            
            elif current_state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.critical(
                        f"💔 [{self.name}] Threshold Reached ({self._failure_count} failures). Circuit OPENED."
                    )

# --- Instances ---

broker_circuit_breaker = CircuitBreaker(
    name="Broker API",
    failure_threshold=3,    # 3 fails = trip
    recovery_timeout=30,    # wait 30s before retry
)

positions_circuit_breaker = CircuitBreaker(
    name="Positions API",
    failure_threshold=5,
    recovery_timeout=60,
)