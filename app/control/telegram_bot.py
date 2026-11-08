"""
Telegram command & control.

- Long-polling (getUpdates): works behind NAT/firewalls, no public port or TLS needed.
- Only the configured chat id is obeyed; everything else is ignored and logged.
- Anything that sends orders or changes risk asks for an inline Confirm tap (expires in 60s).
- Updates that arrived while the server was down are discarded at startup, so a restart can
  never replay an old command.
"""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from sqlalchemy import select

from app.control.commands import CommandError, OrderIntent, parse_order, parse_protection
from app.core.clock import now_ist, start_of_day, today_ist
from app.domain.models import Side
from app.models import Order, StrategyInstance
from app.notifications.telegram import TelegramClient
from app.strategy.engine import EngineError

if TYPE_CHECKING:
    from app.container import Container

logger = logging.getLogger("TelegramBot")

CONFIRM_TTL_SECONDS = 60
Action = Callable[[], Awaitable[str]]

COMMANDS: list[tuple[str, str]] = [
    ("status", "System health, P&L, kill switch"),
    ("positions", "Open positions with live P&L"),
    ("orders", "Today's orders"),
    ("strategies", "Running strategy instances"),
    ("buy", "SYMBOL QTY [@LIMIT] [sl=] [tgt=] [cnc]"),
    ("sell", "SYMBOL QTY [@LIMIT] [sl=] [tgt=] [cnc]"),
    ("exit", "SYMBOL | all - close position(s) at market"),
    ("sl", "SYMBOL [sl=PRICE] [tgt=PRICE] - set/clear protection"),
    ("cancel", "ORDER_ID | all - cancel working orders"),
    ("ltp", "SYMBOL - last traded price"),
    ("stop", "ID|SYMBOL [keep] - stop a strategy (flattens unless keep)"),
    ("resume", "ID - resume a stopped strategy"),
    ("kill", "[reason] - kill switch: flatten all, block entries"),
    ("unkill", "Reset the kill switch"),
    ("help", "Show commands"),
]


@dataclass
class _Pending:
    description: str
    action: Action
    expires_at: float


def h(value: object) -> str:
    return html.escape(str(value))


def fmt_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"


class TelegramBot:
    def __init__(self, client: TelegramClient, chat_id: str, container: "Container"):
        self._client = client
        self._chat_id = str(chat_id)
        self._c = container
        self._offset: Optional[int] = None
        self._pending: dict[str, _Pending] = {}
        self._handlers: dict[str, Callable[[list[str]], Awaitable[Optional[str]]]] = {
            "start": self.cmd_help,
            "help": self.cmd_help,
            "status": self.cmd_status,
            "pnl": self.cmd_status,
            "positions": self.cmd_positions,
            "orders": self.cmd_orders,
            "strategies": self.cmd_strategies,
            "buy": lambda a: self.cmd_order(Side.BUY, a),
            "sell": lambda a: self.cmd_order(Side.SELL, a),
            "exit": self.cmd_exit,
            "sl": self.cmd_protect,
            "cancel": self.cmd_cancel,
            "ltp": self.cmd_ltp,
            "stop": self.cmd_stop,
            "resume": self.cmd_resume,
            "kill": self.cmd_kill,
            "unkill": self.cmd_unkill,
        }

    # ------------------------------------------------------------------ transport
    async def run(self) -> None:
        """Long-poll loop. Restarted by the task supervisor if it ever raises."""
        if self._offset is None:
            await self._skip_backlog()
            try:
                await self._client.set_commands(COMMANDS)
            except Exception as e:
                logger.warning("setMyCommands failed: %s", e)
        while True:
            updates = await self._client.get_updates(self._offset)
            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    await self.handle_update(update)
                except Exception:
                    logger.exception("Failed to handle Telegram update")

    async def _skip_backlog(self) -> None:
        pending = await self._client.get_updates(None, timeout=0)
        self._offset = (pending[-1]["update_id"] + 1) if pending else 0
        if pending:
            logger.warning("Discarded %d Telegram update(s) received while offline", len(pending))

    async def reply(self, text: str, reply_markup: Optional[dict] = None) -> int:
        return await self._client.send_message(self._chat_id, text, reply_markup)

    # ------------------------------------------------------------------ dispatch
    async def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if chat_id != self._chat_id:
            logger.warning("Ignored Telegram message from unauthorised chat %s", chat_id)
            return
        if not text.startswith("/"):
            await self.reply("Send /help for commands.")
            return

        parts = text.split()
        name = parts[0][1:].split("@")[0].lower()
        handler = self._handlers.get(name)
        if handler is None:
            await self.reply(f"Unknown command /{h(name)}. Send /help.")
            return
        logger.info("Telegram command: %s", text)
        try:
            response = await handler(parts[1:])
        except (CommandError, EngineError, ValueError) as e:
            response = f"⚠️ {h(e)}"
        if response:
            await self.reply(response)

    async def _handle_callback(self, cq: dict) -> None:
        chat_id = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
        if chat_id != self._chat_id:
            logger.warning("Ignored callback from unauthorised chat %s", chat_id)
            return
        data = cq.get("data") or ""
        message_id = int((cq.get("message") or {}).get("message_id") or 0)
        verb, _, key = data.partition(":")
        pending = self._pending.pop(key, None)
        await self._client.answer_callback(cq["id"])

        if pending is None:
            await self._client.edit_message(self._chat_id, message_id, "⌛ This request is no longer valid.")
            return
        if verb != "ok":
            await self._client.edit_message(self._chat_id, message_id, f"✖️ Cancelled: {h(pending.description)}")
            return
        if time.monotonic() > pending.expires_at:
            await self._client.edit_message(self._chat_id, message_id, f"⌛ Expired: {h(pending.description)}")
            return

        await self._client.edit_message(self._chat_id, message_id, f"⏳ {h(pending.description)}")
        try:
            result = await pending.action()
        except (CommandError, EngineError, ValueError) as e:
            result = f"⚠️ {h(e)}"
        except Exception as e:
            logger.exception("Confirmed action failed")
            result = f"❌ Failed: {h(e)}"
        await self._client.edit_message(self._chat_id, message_id, result)

    async def confirm(self, description: str, action: Action, details: str = "") -> None:
        self._expire_pending()
        key = secrets.token_hex(6)
        self._pending[key] = _Pending(description, action, time.monotonic() + CONFIRM_TTL_SECONDS)
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirm", "callback_data": f"ok:{key}"},
                    {"text": "✖️ Cancel", "callback_data": f"no:{key}"},
                ]
            ]
        }
        text = f"<b>Confirm?</b>\n{h(description)}" + (f"\n{details}" if details else "")
        await self.reply(text + f"\n<i>expires in {CONFIRM_TTL_SECONDS}s</i>", reply_markup=keyboard)

    def _expire_pending(self) -> None:
        now = time.monotonic()
        for key in [k for k, p in self._pending.items() if p.expires_at < now]:
            self._pending.pop(key, None)

    # ------------------------------------------------------------------ read-only commands
    async def cmd_help(self, args: list[str]) -> str:
        lines = [f"<b>{h(self._c.settings.APP_NAME)}</b> ({self._c.mode})"]
        lines += [f"/{c} {h(d)}" for c, d in COMMANDS]
        return "\n".join(lines)

    async def cmd_status(self, args: list[str]) -> str:
        c = self._c
        pnl = c.risk.day_pnl()
        feed_age = c.feed.seconds_since_data
        feed = "connected" if c.feed.connected else "DOWN"
        if feed_age != float("inf"):
            feed += f", last data {feed_age:.0f}s ago"
        kill = f"🔴 ACTIVE ({h(c.risk.kill_reason)})" if c.risk.kill_switch else "🟢 off"
        failing = c.engine.failures()
        lines = [
            f"<b>{h(c.settings.APP_NAME)}</b> · {c.mode} · {now_ist():%H:%M:%S}",
            f"Broker session: {'✅' if c.kotak.is_logged_in else '❌'}",
            f"Market feed: {feed}",
            f"Kill switch: {kill}",
            "",
            f"<b>Day P&amp;L (net): {fmt_money(pnl.net)}</b>",
            f"Realized {fmt_money(pnl.realized)} · Unrealized {fmt_money(pnl.unrealized)} · "
            f"Charges {pnl.charges:,.2f}",
            f"Loss limit: {c.risk.max_daily_loss:,.0f}",
            "",
            f"Strategies running: {len(c.engine.runners)} · Open positions: {len(c.positions.open_positions())} · "
            f"Working orders: {len(c.oms.open_orders())}",
        ]
        if failing:
            lines.append(f"⚠️ Failing to start: {h(', '.join(failing))}")
        return "\n".join(lines)

    async def cmd_positions(self, args: list[str]) -> str:
        open_positions = self._c.positions.open_positions()
        if not open_positions:
            return "No open positions."
        names = await self._instance_names(list(open_positions))
        lines = ["<b>Open positions</b>"]
        for iid, pos in open_positions.items():
            token = self._c.positions.token_of(iid)
            ltp = self._c.bus.last_price(token) if token else None
            info = self._c.instruments.by_token(token) if token else None
            symbol = info.trading_symbol if info else token
            runner = self._c.engine.runners.get(iid)
            prot = ""
            if runner and (runner.state.get("stop") or runner.state.get("target")):
                prot = f" · SL {runner.state.get('stop') or '-'} TGT {runner.state.get('target') or '-'}"
            pnl = f"{fmt_money(pos.unrealized(ltp))}" if ltp else "n/a"
            lines.append(
                f"<b>{h(symbol)}</b> {pos.qty:+d} @ {pos.avg_price:.2f} · LTP {ltp or '-'} · P&amp;L {pnl}{h(prot)}"
                f"\n  <i>{h(names.get(iid, iid))}</i>"
            )
        return "\n".join(lines)

    async def cmd_orders(self, args: list[str]) -> str:
        async with self._c.sf() as db:
            rows = (
                (
                    await db.execute(
                        select(Order)
                        .where(Order.created_at >= start_of_day(today_ist()))
                        .order_by(Order.created_at.desc())
                        .limit(15)
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            return "No orders today."
        lines = ["<b>Today's orders</b> (latest 15)"]
        for o in rows:
            price = f"@ {o.avg_price:.2f}" if o.filled_qty else (f"lmt {o.price:.2f}" if o.price else "MKT")
            lines.append(
                f"<code>{h(o.id)}</code> {o.created_at.astimezone(now_ist().tzinfo):%H:%M} {o.side} "
                f"{o.filled_qty}/{o.qty} {h(o.symbol)} {price} · <b>{o.status}</b> · {h(o.reason or '')}"
                + (f"\n  {h(o.message)}" if o.status in ("REJECTED", "FAILED") and o.message else "")
            )
        return "\n".join(lines)

    async def cmd_strategies(self, args: list[str]) -> str:
        async with self._c.sf() as db:
            rows = (
                (await db.execute(select(StrategyInstance).where(StrategyInstance.status == "ACTIVE"))).scalars().all()
            )
        if not rows:
            return "No active strategies. Create one via the API, or place a manual order with /buy."
        lines = ["<b>Active strategies</b>"]
        for r in rows:
            pos = self._c.positions.get(r.id)
            pnl = self._c.positions.day_pnl(self._c.bus.last_price, r.id).net
            running = "▶️" if r.id in self._c.engine.runners else "⚠️ not running"
            lines.append(
                f"{running} <b>{h(r.name)}</b> <code>{h(r.id)}</code>\n"
                f"  {h(r.strategy_type)} · {h(r.symbol)} · {r.timeframe}m · pos {pos.qty:+d} · day {fmt_money(pnl)}"
            )
        return "\n".join(lines)

    async def cmd_ltp(self, args: list[str]) -> str:
        if not args:
            raise CommandError("Usage: /ltp SYMBOL")
        info = self._c.instruments.get(args[0])
        if info is None:
            raise CommandError(f"Unknown symbol '{args[0]}'")
        price = await self._ltp(info.token)
        return (
            f"{h(info.trading_symbol)}: <b>{price:.2f}</b>" if price else f"No price yet for {h(info.trading_symbol)}"
        )

    async def _ltp(self, token: str, wait: float = 3.0) -> Optional[float]:
        price = self._c.bus.last_price(token)
        if price is None:
            await self._c.feed.subscribe([token])
            deadline = time.monotonic() + wait
            while price is None and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
                price = self._c.bus.last_price(token)
        return price

    # ------------------------------------------------------------------ trading commands
    async def cmd_order(self, side: Side, args: list[str]) -> None:
        intent = parse_order(side, args)
        info = self._c.instruments.get(intent.symbol)
        if info is None:
            raise CommandError(f"Unknown symbol '{intent.symbol}'")
        intent = OrderIntent(
            intent.side, info.trading_symbol, intent.qty, intent.limit_price, intent.stop, intent.target, intent.product
        )
        ltp = await self._ltp(info.token)
        ref = intent.limit_price or ltp
        details = f"LTP {ltp:.2f} · value ≈ {intent.qty * ref:,.0f}" if ref else "LTP unknown"
        if ltp and intent.stop and intent.limit_price is None:
            wrong = intent.stop >= ltp if side is Side.BUY else intent.stop <= ltp
            if wrong:
                raise CommandError(f"sl {intent.stop} is on the wrong side of LTP {ltp:.2f}")

        async def action() -> str:
            view, runner = await self._c.engine.manual_order(
                intent.symbol,
                intent.side,
                intent.qty,
                limit_price=intent.limit_price,
                stop=intent.stop,
                target=intent.target,
                product=intent.product,
            )
            return (
                f"📤 Sent <code>{h(view.id)}</code>: {h(intent.describe())}\n"
                f"Status: <b>{view.status.value}</b> (fills are reported separately)"
            )

        await self.confirm(intent.describe(), action, h(details))

    async def cmd_exit(self, args: list[str]) -> None:
        if not args:
            raise CommandError("Usage: /exit SYMBOL  or  /exit all")
        if args[0].lower() == "all":
            count = len(self._c.positions.open_positions())

            async def flatten_all() -> str:
                await self._c.engine.flatten_all("telegram /exit all")
                left = len(self._c.positions.open_positions())
                return "✅ All positions closed." if left == 0 else f"⚠️ {left} position(s) still open."

            await self.confirm(f"Close ALL {count} open position(s) at market", flatten_all)
            return

        runner = self._c.engine.runner_for_symbol(args[0])
        if runner is None or runner.position.qty == 0:
            raise CommandError(f"No open position in {args[0].upper()}")
        qty = runner.position.qty

        async def exit_one() -> str:
            flat = await self._c.engine.exit_symbol(args[0])
            return f"✅ {h(runner.symbol)} closed." if flat else f"⚠️ {h(runner.symbol)} exit not confirmed yet."

        await self.confirm(f"Close {runner.symbol} ({qty:+d}) at market [{runner.cfg.name}]", exit_one)

    async def cmd_protect(self, args: list[str]) -> str:
        symbol, updates = parse_protection(args)
        runner = self._c.engine.runner_for_symbol(symbol)
        if runner is None:
            raise CommandError(f"No running instance trades {symbol}")
        levels = await runner.update_protection(updates)
        return f"🛡️ {h(runner.symbol)}: SL {levels['stop'] or '-'} · TGT {levels['target'] or '-'}"

    async def cmd_cancel(self, args: list[str]) -> str:
        if not args:
            raise CommandError("Usage: /cancel ORDER_ID  or  /cancel all")
        if args[0].lower() == "all":
            count = len(self._c.oms.open_orders())
            await self._c.oms.cancel_all()
            return f"🚫 Cancel requested for {count} order(s)."
        order_id = args[0].upper()
        if not any(o.id == order_id for o in self._c.oms.open_orders()):
            raise CommandError(f"No working order {order_id}")
        await self._c.oms.cancel(order_id)
        return f"🚫 Cancel requested for {h(order_id)}."

    # ------------------------------------------------------------------ strategy & risk commands
    async def cmd_stop(self, args: list[str]) -> None:
        if not args:
            raise CommandError("Usage: /stop ID|SYMBOL [keep]")
        keep = len(args) > 1 and args[1].lower() == "keep"
        row = await self._find_instance(args[0])

        async def action() -> str:
            flat = await self._c.engine.stop(row.id, flatten=not keep, reason="stopped via Telegram")
            note = "" if keep or flat else " ⚠️ position NOT flat"
            return f"⏹ Stopped {h(row.name)}.{note}"

        verb = "keep position" if keep else "close position"
        await self.confirm(f"Stop {row.name} ({verb})", action)

    async def cmd_resume(self, args: list[str]) -> None:
        if not args:
            raise CommandError("Usage: /resume ID")
        row = await self._find_instance(args[0], active=False)

        async def action() -> str:
            await self._c.engine.resume(row.id)
            running = row.id in self._c.engine.runners
            return f"▶️ {h(row.name)} {'running' if running else 'resumed but not running yet (see /status)'}."

        await self.confirm(f"Resume {row.name}", action)

    async def cmd_kill(self, args: list[str]) -> None:
        reason = " ".join(args) or "manual (Telegram)"

        async def action() -> str:
            await self._c.risk.trip(reason)
            return "🔴 Kill switch ACTIVE. Flattening all positions; entries blocked for today."

        await self.confirm(f"KILL SWITCH: flatten everything and block entries ({reason})", action)

    async def cmd_unkill(self, args: list[str]) -> None:
        if not self._c.risk.kill_switch:
            raise CommandError("Kill switch is not active.")

        async def action() -> str:
            await self._c.risk.reset()
            return "🟢 Kill switch reset. Entries allowed."

        await self.confirm("Reset kill switch and allow new entries", action)

    # ------------------------------------------------------------------ helpers
    async def _find_instance(self, key: str, active: bool = True) -> StrategyInstance:
        async with self._c.sf() as db:
            row = await db.get(StrategyInstance, key)
            if row is None:
                info = self._c.instruments.get(key)
                if info is not None:
                    stmt = select(StrategyInstance).where(StrategyInstance.token == info.token)
                    if active:
                        stmt = stmt.where(StrategyInstance.status == "ACTIVE")
                    row = (await db.execute(stmt.order_by(StrategyInstance.updated_at.desc()))).scalars().first()
        if row is None:
            raise CommandError(f"No {'active ' if active else ''}strategy matches '{key}'")
        return row

    async def _instance_names(self, ids: list[str]) -> dict[str, str]:
        async with self._c.sf() as db:
            rows = (await db.execute(select(StrategyInstance).where(StrategyInstance.id.in_(ids)))).scalars().all()
        return {r.id: r.name for r in rows}
