"""Maintainer observability: forward domain events to Discord channels.

Each guild (and optionally a central maintainer guild) can pick a
channel per event category — e.g. all gacha pulls to #gacha-log, all
economy mutations to #eco-log, errors to #bot-errors. Routing lives in
the ``log_channels`` table and is managed via ``!logchannel`` commands.

All sends pass through the rate-limit-aware :class:`OutboundLimiter`
and are best-effort: a missing-permission or deleted channel logs a
warning and never breaks gameplay.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from bot.core.events import EVENT_CATEGORIES, EventBus, GameEvent
from bot.core.ratelimit import OutboundLimiter
from bot.ui.theme import Theme

if TYPE_CHECKING:
    from bot.core.database import Database

logger = logging.getLogger("gacha.observability")

#: "all" receives every category regardless of specific mappings.
WILDCARD_CATEGORY: str = "all"

VALID_CATEGORIES: tuple[str, ...] = EVENT_CATEGORIES + (WILDCARD_CATEGORY,)

_QUEUE_MAX: int = 512          # events waiting to be delivered
_DRAIN_TIMEOUT: float = 10.0   # seconds given to flush on shutdown


class DiscordSink:
    """Routes :class:`GameEvent` objects to configured Discord channels.

    Delivery is fire-and-forget: handlers enqueue events and a background
    worker performs the (potentially slow, rate-limited) Discord sends.
    Gameplay therefore never awaits Discord HTTP — a 429 backoff on a log
    channel cannot stall a player's command.
    """

    def __init__(self, bot: discord.Client, db: "Database", bus: EventBus, maintainer_guild_id: int | None) -> None:
        self._bot = bot
        self._db = db
        self._bus = bus
        self._maintainer_guild_id = maintainer_guild_id
        self._limiter = OutboundLimiter(max_parallel=2)
        # guild_id -> {category: channel_id}
        self._routes: dict[int, dict[str, int]] = {}
        self._handler = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._worker: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        await self._load_routes()
        self._handler = self._handle
        await self._bus.subscribe(self._handler)
        self._worker = asyncio.create_task(self._drain(), name="discord-sink")
        logger.info("Discord sink started (%d guilds routed)", len(self._routes))

    async def stop(self) -> None:
        if self._handler is not None:
            await self._bus.unsubscribe(self._handler)
            self._handler = None
        if self._worker is not None:
            try:
                await self._queue.put(None)  # sentinel: flush then exit
            except asyncio.QueueFull:
                self._worker.cancel()
            try:
                await asyncio.wait_for(self._worker, timeout=_DRAIN_TIMEOUT)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker.cancel()
            self._worker = None

    async def _load_routes(self) -> None:
        self._routes.clear()
        rows = await self._db.fetch_all("SELECT guild_id, category, channel_id FROM log_channels")
        for row in rows:
            self._routes.setdefault(int(row["guild_id"]), {})[row["category"]] = int(row["channel_id"])

    # -- routing management (used by admin cog) ---------------------------------

    async def set_channel(self, guild_id: int, category: str, channel_id: int) -> None:
        await self._db.execute(
            """
            INSERT INTO log_channels (guild_id, category, channel_id) VALUES (:g, :c, :ch)
            ON CONFLICT (guild_id, category) DO UPDATE SET channel_id = :ch
            """,
            {"g": guild_id, "c": category, "ch": channel_id},
        )
        await self._load_routes()

    async def remove_channel(self, guild_id: int, category: str) -> bool:
        changed = await self._db.execute(
            "DELETE FROM log_channels WHERE guild_id = :g AND category = :c",
            {"g": guild_id, "c": category},
        )
        await self._load_routes()
        return bool(changed)

    async def list_channels(self, guild_id: int) -> dict[str, int]:
        return dict(self._routes.get(guild_id, {}))

    # -- event handling ---------------------------------------------------------

    async def _handle(self, event: GameEvent) -> None:
        """Bus handler: enqueue for the worker; drop (with a log) under pressure."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Event sink queue full (%d) — dropping %s/%s",
                _QUEUE_MAX, event.category, event.action,
            )

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                break
            try:
                await self._dispatch(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sink dispatch failed for %s/%s", event.category, event.action)
            finally:
                self._queue.task_done()

    async def _dispatch(self, event: GameEvent) -> None:
        channel_ids = self._target_channels(event)
        if not channel_ids:
            return
        embed = self._embed(event)
        for channel_id in channel_ids:
            await self._limiter.run(lambda cid=channel_id: self._send(cid, embed))

    def _target_channels(self, event: GameEvent) -> set[int]:
        targets: set[int] = set()

        def collect(guild_id: int) -> None:
            routes = self._routes.get(guild_id, {})
            if event.category in routes:
                targets.add(routes[event.category])
            if WILDCARD_CATEGORY in routes:
                targets.add(routes[WILDCARD_CATEGORY])
            if event.category == "error" and "error" in routes:
                targets.add(routes["error"])

        if event.guild_id is not None:
            collect(event.guild_id)
        if self._maintainer_guild_id is not None:
            collect(self._maintainer_guild_id)
        return targets

    def _embed(self, event: GameEvent) -> discord.Embed:
        colour = event.colour if event.colour is not None else Theme.info
        who = f"<@{event.user_id}>" if event.user_id is not None else "unknown"
        where = f"guild `{event.guild_id}`" if event.guild_id is not None else "global"
        embed = Theme.embed(
            f"{event.category} \u00b7 {event.action}",
            event.message or "\u2014",
            colour,
        )
        embed.add_field(name="player", value=who, inline=True)
        embed.add_field(name="scope", value=where, inline=True)
        embed.add_field(
            name="at", value=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True,
        )
        return embed

    async def _send(self, channel_id: int, embed: discord.Embed) -> None:
        channel = self._bot.get_channel(channel_id)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Missing permissions to log into channel %d", channel_id)
        except discord.HTTPException as exc:
            logger.warning("Failed to log into channel %d: %s", channel_id, exc)
