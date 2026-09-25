"""Service layer base class.

Services own all game logic and persistence; Discord cogs are thin
transport adapters. This ABC bundles shared dependencies — database,
content registry, settings, event bus — so concrete services stay
focused and never touch Discord objects.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.config import GameSettings
    from bot.content.registry import ContentRegistry
    from bot.core.cooldowns import CooldownManager
    from bot.core.database import Database
    from bot.core.events import EventBus


class BaseService(ABC):
    """Common contract: database + content + settings + event bus."""

    log_name: str = "gacha.service"

    def __init__(
        self,
        db: "Database",
        content: "ContentRegistry",
        settings: "GameSettings",
        bus: "EventBus",
        cooldowns: "CooldownManager",
    ) -> None:
        self.db = db
        self.content = content
        self.settings = settings
        self.bus = bus
        self.cooldowns = cooldowns
        self.log = logging.getLogger(self.log_name)
        self._post_init()

    def _post_init(self) -> None:  # hook for subclasses
        pass

    @abstractmethod
    async def on_start(self) -> None:
        """Called once when the bot is ready (e.g. start loops)."""

    async def close(self) -> None:  # optional override
        pass
