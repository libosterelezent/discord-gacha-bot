"""Service layer base class.

Services own all game logic and persistence; Discord cogs are thin
transport adapters. This ABC provides shared dependencies (database,
config, logger) so concrete services stay focused.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from bot.core.database import Database


class BaseService(ABC):
    """Common contract: any service needs a database handle."""

    log_name: str = "gacha.service"

    def __init__(self, db: Database) -> None:
        self.db = db
        self.log = logging.getLogger(self.log_name)
        self._post_init()

    def _post_init(self) -> None:  # hook for subclasses
        pass

    @abstractmethod
    async def on_start(self) -> None:
        """Called once when the bot is ready (e.g. start loops)."""
