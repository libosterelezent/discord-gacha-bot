"""Rate-limit-aware outbound sending.

discord.py already tracks Discord's per-route buckets from the
``X-RateLimit-*`` response headers and pre-emptively waits before a
bucket resets, so ordinary calls never hit 429. This module adds the
safety net production bots need on top:

* a semaphore capping concurrent sends,
* explicit handling of :class:`discord.RetryAfter` (global 429) with
  bounded retries and jitter,
* drop-and-log on hard HTTP errors so a dead channel can't stall game
  logic.
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


class OutboundLimiter:
    """Serialise Discord sends with RetryAfter respect."""

    __slots__ = ("_semaphore",)

    def __init__(self, max_parallel: int = 3) -> None:
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def run(self, send: Callable[[], Awaitable[T]]) -> T | None:
        """Execute `send`, retrying on RetryAfter; None when dropped."""
        async with self._semaphore:
            attempt = 0
            while True:
                try:
                    return await send()
                except discord.RetryAfter as exc:
                    attempt += 1
                    if attempt > _MAX_RETRIES:
                        logger.warning(
                            "Outbound send dropped after %d rate-limit retries", attempt - 1
                        )
                        return None
                    delay = float(exc.retry_after) + random.uniform(0.0, _JITTER)
                    logger.info(
                        "Rate limited (attempt %d/%d) — backing off %.1fs",
                        attempt, _MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                except discord.HTTPException as exc:
                    logger.warning("Outbound send failed (%s) — dropping", exc)
                    return None
                except asyncio.CancelledError:
                    raise
