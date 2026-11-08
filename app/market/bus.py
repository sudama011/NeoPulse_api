"""
In-process tick pub/sub.

Each subscriber gets its own bounded queue, so one slow strategy can never block the
feed or other strategies. When a subscriber falls behind, its oldest tick is dropped
(latest price wins), which is the right trade-off for price data.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Optional

from app.domain.models import Tick

logger = logging.getLogger("TickBus")


class Subscription:
    def __init__(self, bus: "TickBus", token: str, maxsize: int):
        self._bus = bus
        self.token = token
        self.queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def offer(self, tick: Tick) -> None:
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(tick)

    async def get(self) -> Tick:
        return await self.queue.get()

    def close(self) -> None:
        self._bus.unsubscribe(self)

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()


class TickBus:
    def __init__(self, subscriber_queue_size: int = 1000):
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._last: dict[str, Tick] = {}
        self._queue_size = subscriber_queue_size

    def subscribe(self, token: str) -> Subscription:
        sub = Subscription(self, str(token), self._queue_size)
        self._subs[sub.token].add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.token)
        if subs:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.token]

    def publish(self, tick: Tick) -> None:
        self._last[tick.token] = tick
        for sub in tuple(self._subs.get(tick.token, ())):
            sub.offer(tick)

    def last_tick(self, token: str) -> Optional[Tick]:
        return self._last.get(str(token))

    def last_price(self, token: str) -> Optional[float]:
        tick = self._last.get(str(token))
        return tick.ltp if tick else None
