"""Reusable decorators: structured timing, error translation and
in-memory command cooldowns built on top of `functools.wraps`.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, TypeVar

from bot.core.exceptions import CooldownError

logger = logging.getLogger("gacha.decorators")

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def timed(logger_: logging.Logger | None = None, slow_ms: float = 250.0) -> Callable[[F], F]:
    """Log coroutine execution time; warn when exceeding `slow_ms`."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                if elapsed_ms > slow_ms:
                    (logger_ or logger).warning(
                        "%s took %.1fms (threshold %.1fms)", func.__qualname__, elapsed_ms, slow_ms
                    )
                else:
                    (logger_ or logger).debug("%s finished in %.1fms", func.__qualname__, elapsed_ms)

        return wrapper  # type: ignore[return-value]

    return decorator


def to_domain_errors(*transitions: tuple[type[Exception], type[Exception]]) -> Callable[[F], F]:
    """Translate low-level exceptions into domain exceptions.

    Usage: ``@to_domain_errors((sqlite3.Error, DatabaseError))``
    Each tuple is ``(source_exception, target_exception)``.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except tuple(src for src, _ in transitions) as exc:
                for src, target in transitions:
                    if isinstance(exc, src):
                        raise target(original=exc) from exc
                raise

        return wrapper  # type: ignore[return-value]

    return decorator


class SlidingWindowRateLimiter:
    """Per-key sliding-window rate limiter (O(1) memory amortised)."""

    __slots__ = ("_window", "_max_calls", "_hits")

    def __init__(self, window_seconds: float, max_calls: int) -> None:
        self._window = window_seconds
        self._max_calls = max_calls
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> float:
        """Record a hit for `key`. Raise :class:`CooldownError` when limited.

        Returns seconds remaining until the key would be allowed again
        on failure (the exception carries it) or 0.0 on success.
        """
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._max_calls:
            retry_after = self._window - (now - hits[0])
            raise CooldownError(max(retry_after, 0.1))
        hits.append(now)
        if not hits:  # pragma: no cover - defensive
            del self._hits[key]
        return 0.0


def rate_limited(window_seconds: float, max_calls: int, key_arg: str = "user_id") -> Callable[[F], F]:
    """Decorator applying :class:`SlidingWindowRateLimiter` per player.

    `key_arg` names the keyword/positional argument identifying the caller.
    """

    limiter = SlidingWindowRateLimiter(window_seconds, max_calls)

    def decorator(func: F) -> F:
        signature_keys = func.__code__.co_varnames[: func.__code__.co_argcount]

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = dict(zip(signature_keys, args))
            bound.update(kwargs)
            key = str(bound.get(key_arg, "unknown"))
            limiter.check(key)
            return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def run_safe(default: Any = None, reraise: bool = True) -> Callable[[F], F]:
    """Catch unexpected exceptions, log full traceback, then either
    re-raise (default) or return `default` for best-effort paths."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unhandled error in %s", func.__qualname__)
                if reraise:
                    raise
                return default

        return wrapper  # type: ignore[return-value]

    return decorator
