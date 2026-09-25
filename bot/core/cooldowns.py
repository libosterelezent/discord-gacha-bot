"""Modular, maintainer-configurable cooldowns.

All cooldown windows live in ``config/game.json`` under
``cooldowns.fixed`` (one-shot timers like daily/hunt) and
``cooldowns.windows`` (sliding-window limits like pull). Changing a
value there (plus ``!reload_settings``) retunes the bot without code
changes or restarts.

Keys are scope-aware: ``(guild_id, user_id, action)``, so cooldowns
never leak between servers in guild-scoped mode.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from bot.config import GameSettings
from bot.core.exceptions import CooldownError


class CooldownManager:
    """In-memory cooldown store backed by game.json values."""

    __slots__ = ("_settings", "_until", "_hits")

    def __init__(self, settings: GameSettings) -> None:
        self._settings = settings
        # fixed cooldowns: key -> monotonic deadline
        self._until: dict[tuple[int, int, str], float] = {}
        # sliding windows: key -> deque of hit timestamps
        self._hits: dict[tuple[int, int, str], deque[float]] = defaultdict(deque)

    # -- introspection -------------------------------------------------------

    @staticmethod
    def key(guild_id: int, user_id: int, action: str) -> tuple[int, int, str]:
        return (guild_id, user_id, action)

    def remaining(self, guild_id: int, user_id: int, action: str) -> float:
        """Seconds left on a fixed cooldown; 0.0 when ready."""
        until = self._until.get(self.key(guild_id, user_id, action), 0.0)
        return max(0.0, until - time.monotonic())

    # -- fixed cooldowns -----------------------------------------------------

    def check(self, guild_id: int, user_id: int, action: str) -> None:
        """Raise :class:`CooldownError` if `action` is still cooling down."""
        remaining = self.remaining(guild_id, user_id, action)
        if remaining > 0:
            raise CooldownError(remaining)

    def trigger(self, guild_id: int, user_id: int, action: str, *, scale: float = 1.0) -> float:
        """Start (or restart) the fixed cooldown for `action`."""
        seconds = self._settings.cooldown_seconds(action) * scale
        self._until[self.key(guild_id, user_id, action)] = time.monotonic() + seconds
        return seconds

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
