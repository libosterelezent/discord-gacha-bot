"""Reusable decorator: structured coroutine timing built on functools.wraps."""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Awaitable, Callable, TypeVar

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
