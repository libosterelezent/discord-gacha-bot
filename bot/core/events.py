"""Domain event bus: decouples game systems from observers.

Services publish semantic events (``daily claimed``, ``pull``,
``hunt`` …) without knowing who listens. Observers — rotating file logs,
the maintainer Discord sink, future metrics — subscribe independently.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Final, Mapping

logger = logging.getLogger("gacha.events")

EventHandler: Final = Callable[["GameEvent"], Awaitable[None]]

#: Well-known event categories used by the Discord logging sink.
EVENT_CATEGORIES: Final[tuple[str, ...]] = (
    "economy", "gacha", "hunt", "huntbot", "equipment", "upgrades", "admin", "records", "error",
)


@dataclass(frozen=True, slots=True)
class GameEvent:
    """An immutable thing that happened in the game."""

    category: str                      # one of EVENT_CATEGORIES (or custom)
    action: str                        # e.g. "daily", "pull", "hunt", "grant"
    guild_id: int | None = None
    user_id: int | None = None
    message: str = ""                  # human-readable one-liner for sinks
    colour: int | None = None          # optional presentation hint
    fields: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.fields, MappingProxyType):
            object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


class EventBus:
    """Fan-out pub/sub with per-handler isolation.

    A failing handler is logged and skipped — a broken logger must never
    break gameplay.
    """

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, handler: EventHandler) -> None:
        async with self._lock:
            self._handlers.append(handler)

    async def unsubscribe(self, handler: EventHandler) -> None:
        async with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    async def publish(self, event: GameEvent) -> None:
        """Invoke all handlers concurrently; each guarded against errors."""
        handlers = list(self._handlers)
        if not handlers:
            return
        results = await asyncio.gather(
            *(self._safe_call(h, event) for h in handlers), return_exceptions=False
        )
        del results

    @staticmethod
    async def _safe_call(handler: EventHandler, event: GameEvent) -> None:
        try:
            await handler(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Event handler %r failed for %s/%s",
                getattr(handler, "__qualname__", handler), event.category, event.action,
            )
