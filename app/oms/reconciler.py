"""
Position reconciliation against the broker (live mode only).

Our PositionBook is derived from our own fills. The broker can diverge from it through
events we never see as fills: broker auto square-off, manual trades in the app, a fill
that happened while we were down and whose order we could not match, etc.

Policy:
- Only tokens owned by exactly one strategy instance are reconciled.
- Instances with working orders are skipped (fills may be in flight).
- A mismatch must persist across two consecutive checks before the broker's figure is
  adopted (avoids racing a fill that is about to be applied). At startup, after orders
  have been reconciled, a single check is enough.
- Broker positions in tokens we do not own are reported once per day, never touched.
"""

import logging
from collections import defaultdict

from app.broker.base import Broker
from app.core.clock import today_ist
from app.notifications.notifier import Notifier
from app.oms.order_manager import OrderManager
from app.portfolio.book import PositionBook

logger = logging.getLogger("Reconciler")


class PositionReconciler:
    def __init__(
        self,
        broker: Broker,
        positions: PositionBook,
        oms: OrderManager,
        notifier: Notifier,
        products: tuple[str, ...] = ("MIS", "CNC", "NRML"),
    ):
        self._broker = broker
        self._positions = positions
        self._oms = oms
        self._notifier = notifier
        self._products = set(products)
        self._suspect: dict[str, int] = {}
        self._reported_untracked: set[tuple[str, str]] = set()

    async def reconcile(self, startup: bool = False) -> None:
        try:
            broker_positions = await self._broker.positions()
        except Exception as e:
            logger.warning("Position reconciliation skipped: %s", e)
            return

        broker_net: dict[str, int] = defaultdict(int)
        broker_avg: dict[str, float] = {}
        symbols: dict[str, str] = {}
        for p in broker_positions:
            if p.product in self._products:
                broker_net[p.token] += p.net_qty
                broker_avg[p.token] = p.avg_price
                symbols[p.token] = p.symbol

        owners: dict[str, list[str]] = defaultdict(list)
        for instance_id in set(self._positions.open_positions()) | self._registered_ids():
            token = self._positions.token_of(instance_id)
            if token:
                owners[token].append(instance_id)

        for token, ids in owners.items():
            if len(ids) != 1:
                continue
            iid = ids[0]
            ours = self._positions.get(iid).qty
            theirs = broker_net.get(token, 0)
            if ours == theirs:
                self._suspect.pop(iid, None)
                continue
            if self._oms.open_orders(iid):
                continue
            seen = self._suspect.get(iid, 0) + 1
            self._suspect[iid] = seen
            if startup or seen >= 2:
                await self._positions.adopt(iid, theirs, broker_avg.get(token, 0.0))
                self._suspect.pop(iid, None)
                msg = (
                    f"Position mismatch on {symbols.get(token, token)} ({iid}): "
                    f"ours={ours} broker={theirs}. Adopted broker."
                )
                logger.warning(msg)
                self._notifier.send(msg, level="WARNING")

        today = today_ist().isoformat()
        for token, qty in broker_net.items():
            key = (today, token)
            if qty != 0 and token not in owners and key not in self._reported_untracked:
                self._reported_untracked.add(key)
                self._notifier.send(
                    f"Untracked broker position: {symbols.get(token, token)} qty={qty} (not managed by NeoPulse)",
                    level="WARNING",
                )

    def _registered_ids(self) -> set[str]:
        return set(self._positions.registered_ids())
