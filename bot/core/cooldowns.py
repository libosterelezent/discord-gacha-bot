"""Modular, maintainer-configurable cooldowns.

All cooldown windows live in ``config/game.json`` under
``cooldowns.fixed`` (one-shot timers like daily/hunt) and
``cooldowns.windows`` (sliding-window limits like pull). Changing a
value there (plus ``!reload_settings``) retunes the bot without code
changes or restarts.

Keys are scope-aware: ``(guild_id, user_id, action)``, so cooldowns
never leak between servers in guild-scoped mode.

Fixed cooldowns are **persistent**: deadlines are stored wall-clock in
the ``cooldowns`` table and reloaded at boot, so restarts no longer
reset daily/work/hunt timers. Sliding windows are short-lived by
design and stay in memory only.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bot.config import GameSettings
from bot.core.exceptions import CooldownError

if TYPE_CHECKING:
    from bot.core.database import Database

_SQL_UPSERT_COOLDOWN = """
INSERT INTO cooldowns (guild_id, user_id, action, expires_at)
VALUES (:g, :u, :a, :t)
ON CONFLICT (guild_id, user_id, action) DO UPDATE SET expires_at = :t
"""
_SQL_PURGE_EXPIRED = "DELETE FROM cooldowns WHERE expires_at <= :now"

_PRUNE_INTERVAL: int = 512  # memory sweeps every N triggers


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _parse_iso(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class CooldownManager:
    """In-memory cooldown store backed by game.json values and the database."""

    __slots__ = ("_settings", "_until", "_hits", "_db", "_trigger_count")

    def __init__(self, settings: GameSettings, db: "Database | None" = None) -> None:
        self._settings = settings
        # fixed cooldowns: key -> wall-clock deadline (epoch seconds)
        self._until: dict[tuple[int, int, str], float] = {}
        # sliding windows: key -> deque of monotonic hit timestamps
        self._hits: dict[tuple[int, int, str], deque[float]] = defaultdict(deque)
        self._db = db
        self._trigger_count = 0

    # -- lifecycle -----------------------------------------------------------

    async def load(self) -> None:
        """Reload persisted fixed cooldowns and purge expired rows."""
        if self._db is None:
            return
        now = time.time()
        await self._db.execute(_SQL_PURGE_EXPIRED, {"now": _iso(now)})
        rows = await self._db.fetch_all("SELECT guild_id, user_id, action, expires_at FROM cooldowns")
        for row in rows:
            deadline = _parse_iso(row["expires_at"])
            if deadline is not None and deadline > now:
                self._until[
                    (int(row["guild_id"]), int(row["user_id"]), row["action"])
                ] = deadline

    # -- introspection -------------------------------------------------------

    @staticmethod
    def key(guild_id: int, user_id: int, action: str) -> tuple[int, int, str]:
        return (guild_id, user_id, action)

    def remaining(self, guild_id: int, user_id: int, action: str) -> float:
        """Seconds left on a fixed cooldown; 0.0 when ready."""
        until = self._until.get(self.key(guild_id, user_id, action), 0.0)
        return max(0.0, until - time.time())

    # -- fixed cooldowns -----------------------------------------------------

    def check(self, guild_id: int, user_id: int, action: str) -> None:
        """Raise :class:`CooldownError` if `action` is still cooling down."""
        remaining = self.remaining(guild_id, user_id, action)
        if remaining > 0:
            raise CooldownError(remaining)

    async def trigger(self, guild_id: int, user_id: int, action: str, *, scale: float = 1.0) -> float:
        """Start (or restart) the fixed cooldown for `action` and persist it."""
        seconds = self._settings.cooldown_seconds(action) * scale
        deadline = time.time() + seconds
        self._until[self.key(guild_id, user_id, action)] = deadline
        self._trigger_count += 1
        if self._trigger_count % _PRUNE_INTERVAL == 0:
            self._prune(now=deadline)
        if self._db is not None and seconds > 0:
            await self._db.execute(
                _SQL_UPSERT_COOLDOWN,
                {"g": guild_id, "u": user_id, "a": action, "t": _iso(deadline)},
            )
        return seconds

    def _prune(self, *, now: float) -> None:
        """Drop expired in-memory deadlines so the map cannot grow forever."""
        expired = [k for k, until in self._until.items() if until <= now]
        for k in expired:
            del self._until[k]

    # -- sliding windows -----------------------------------------------------

    def check_window(self, guild_id: int, user_id: int, action: str) -> None:
        """Raise :class:`CooldownError` when the sliding-window budget is spent."""
        max_calls, window = self._settings.window(action)
        if max_calls <= 0 or window <= 0:
            return
        key = self.key(guild_id, user_id, action)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= max_calls:
            retry_after = window - (now - hits[0])
            raise CooldownError(max(retry_after, 0.1))
        hits.append(now)
