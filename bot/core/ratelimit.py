"""Rate-limit-aware outbound sending.

discord.py 2.x already tracks Discord's per-route buckets from the
``X-RateLimit-*`` response headers, pre-emptively waits before a bucket
resets, and retries 429s internally. This module adds the safety net
production bots need on top:

* a semaphore capping concurrent sends,
* bounded, jittered retries for 429s that still surface (e.g. global
  rate limits) using the response's reset-after header when present,
* drop-and-log on hard HTTP errors so a dead channel can't stall game
  logic.

Note: discord.py 1.x's ``discord.RetryAfter`` no longer exists in 2.x —
429s arrive as :class:`discord.HTTPException` with ``status == 429``.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import discord

logger = logging.getLogger("gacha.ratelimit")

T = TypeVar("T")

_MAX_RETRIES: int = 3
_JITTER: float = 0.25
_DEFAULT_BACKOFF: float = 1.0


def _retry_delay(exc: discord.HTTPException) -> float:
    """Best-effort delay from the 429 response headers."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        value = headers.get("X-RateLimit-Reset-After") or headers.get("Retry-After")
        if value:
            return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    return _DEFAULT_BACKOFF


class OutboundLimiter:
    """Serialise Discord sends with 429-aware bounded retries."""

    __slots__ = ("_semaphore",)

    def __init__(self, max_parallel: int = 3) -> None:
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def run(self, send: Callable[[], Awaitable[T]]) -> T | None:
        """Execute `send`, retrying 429s; None when dropped."""
        async with self._semaphore:
            attempt = 0
            while True:
                try:
                    return await send()
                except discord.HTTPException as exc:
                    if exc.status == 429 and attempt < _MAX_RETRIES:
                        attempt += 1
                        delay = _retry_delay(exc) + random.uniform(0.0, _JITTER)
                        logger.info(
                            "Rate limited (attempt %d/%d) — backing off %.1fs",
                            attempt, _MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("Outbound send failed (%s) — dropping", exc)
                    return None
                except asyncio.CancelledError:
                    raise
