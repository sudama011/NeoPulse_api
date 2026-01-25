import logging
import asyncio
from datetime import datetime, timedelta
from typing import Callable, Any, Optional
from enum import Enum

logger = logging.getLogger("CircuitBreaker")


class CircuitState(str, Enum):
    """Circuit breaker state machine."""
    CLOSED = "CLOSED"           # Normal operation
    OPEN = "OPEN"               # Failing - block all calls
    HALF_OPEN = "HALF_OPEN"     # Testing recovery


class CircuitBreaker:
    """
    Circuit Breaker Pattern for broker API calls.
    
    Prevents cascading failures when broker API is down/slow.
    
    States:
    - CLOSED: Normal operation, calls go through
    - OPEN: Too many failures, block calls and alert
    - HALF_OPEN: Testing if broker recovered
    
    Transitions:
    - CLOSED → OPEN: When failure_count >= threshold
    - OPEN → HALF_OPEN: After recovery_timeout seconds
    - HALF_OPEN → CLOSED: If next call succeeds
    - HALF_OPEN → OPEN: If next call fails
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Identifier for logging
            failure_threshold: Open after N consecutive failures
            recovery_timeout: Seconds before attempting HALF_OPEN recovery
            expected_exception: Exception type to count as failure
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        # State tracking
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_failure_reason = ""
        self.success_count = 0

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Async function to execute
            *args, **kwargs: Arguments to pass
            
        Returns:
            Function result
            
        Raises:
            Exception: If function fails or circuit is OPEN
        """
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                logger.warning(
                    f"🟡 [{self.name}] Circuit breaker HALF_OPEN - "
                    f"attempting recovery (failed {self.failure_count} times)"
                )
            else:
                raise Exception(
                    f"🔴 [{self.name}] Circuit breaker OPEN. "
                    f"Broker unavailable. Last error: {self.last_failure_reason}"
                )
        
        try:
            # Execute the function
            result = await func(*args, **kwargs)
            
            # Success
            if self.state == CircuitState.HALF_OPEN:
                # Recovery successful!
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count += 1
                logger.info(
                    f"🟢 [{self.name}] Circuit breaker CLOSED - "
                    f"broker recovered (recovered on attempt #{self.success_count})"
                )
            elif self.state == CircuitState.CLOSED:
                # Normal success
                pass
            
            return result
            
        except self.expected_exception as e:
            # Failure
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            self.last_failure_reason = str(e)
            
            logger.error(
                f"❌ [{self.name}] Broker call failed "
                f"({self.failure_count}/{self.failure_threshold}): {e}"
            )
            
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.critical(
                    f"🔴 [{self.name}] Circuit breaker OPEN - "
                    f"threshold exceeded after {self.failure_count} failures"
                )
            
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if not self.last_failure_time:
            return True
        
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def get_status(self) -> dict:
        """Return circuit breaker status."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_failure_reason": self.last_failure_reason,
        }


# Global circuit breakers for broker operations
broker_circuit_breaker = CircuitBreaker(
    name="Broker API",
    failure_threshold=3,
    recovery_timeout=30,
)

positions_circuit_breaker = CircuitBreaker(
    name="Get Positions",
    failure_threshold=5,
    recovery_timeout=60,
)

limits_circuit_breaker = CircuitBreaker(
    name="Get Limits",
    failure_threshold=5,
    recovery_timeout=60,
)
