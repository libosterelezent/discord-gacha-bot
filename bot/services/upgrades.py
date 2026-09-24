"""Upgrade catalog and upgrade service.

An :class:`UpgradeSpec` is a declarative descriptor (dataclass) with a
cost curve function. Effects are aggregated into a dict consumed by
`StatProfile.compose`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bot.config import CONFIG, CONSTANTS
from bot.core.database import Database
from bot.core.exceptions import InsufficientFundsError, UpgradeError
from bot.models.player import Player
from bot.services.base import BaseService


@dataclass(frozen=True, slots=True)
class UpgradeSpec:
    key: str
    name: str
    description: str
    emoji: str
    base_cost: int
    cost_growth: float          # exponential cost curve
    effect_per_level: float
    max_level: int = CONFIG.max_upgrade_level

    def cost(self, current_level: int) -> int:
        """Cost to go from `current_level` to +1 (exponential curve)."""
        return int(self.base_cost * (self.cost_growth ** current_level))

    def effect(self, level: int) -> float:
        return self.effect_per_level * level


class UpgradeCatalog:
    """Static registry of available upgrades (pattern: catalog object)."""

    _specs: dict[str, UpgradeSpec] = {
        u.key: u
        for u in (
            UpgradeSpec("luck",      "Fortune",       "Boosts rare drops in gacha & hunts.", "\U0001f380", 2_000,   1.35, 0.02),
            UpgradeSpec("greed",     "Greed",         "+5% coin gains per level.",            "\U0001f999", 2_500,   1.38, 0.05),
            UpgradeSpec("swiftness", "Swiftness",     "Reduces hunt cooldown by 2%/level.",   "\u26a1",       3_000,   1.40, 0.02),
            UpgradeSpec("power",     "Battle Power",  "+3 attack-equivalent per level.",      "\u2694\ufe0f",  1_800,   1.33, 3.0),
            UpgradeSpec("harvest",   "Harvest",       "Huntbot yields +4% per level.",        "\U0001f916", 5_000,   1.45, 0.04),
        )
    }

    @classmethod
    def get(cls, key: str) -> UpgradeSpec:
        try:
            return cls._specs[key]
        except KeyError:
            raise UpgradeError(f"Unknown upgrade `{key}`.", upgrade_key=key) from None

    @classmethod
    def all(cls) -> list[UpgradeSpec]:
        return list(cls._specs.values())

    @classmethod
    def effects(cls) -> dict[str, float]:
        return {u.key: u.effect_per_level for u in cls._specs.values()}


class UpgradeService(BaseService):
    log_name = "gacha.upgrades"

    def __init__(self, db: Database, get_player: Callable) -> None:
        self._get_player = get_player  # dependency injected from EconomyService
        super().__init__(db)

    async def on_start(self) -> None:
        self.log.info("Upgrade service ready (%d upgrades)", len(UpgradeCatalog.all()))

    async def levels_for(self, user_id: int) -> dict[str, int]:
        rows = await self.db.fetch_all(
            "SELECT upgrade_key, level FROM upgrades WHERE user_id = ?", (str(user_id),)
        )
        return {r["upgrade_key"]: r["level"] for r in rows}

    async def buy(self, player: Player, key: str) -> tuple[int, int]:
        """Purchase one level; returns (new_level, cost)."""
        spec = UpgradeCatalog.get(key)
        current = (await self.levels_for(player.user_id)).get(key, 0)
        if current >= spec.max_level:
            raise UpgradeError(
                f"`{spec.name}` is already maxed (level {current}/{spec.max_level}).",
                upgrade_key=key, level=current,
            )
        cost = spec.cost(current)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)

        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE players SET balance = balance - ?, updated_at = datetime('now') WHERE user_id = ?",
                (cost, str(player.user_id)),
            )
            await conn.execute(
                """
                INSERT INTO upgrades (user_id, upgrade_key, level) VALUES (?, ?, 1)
                ON CONFLICT(user_id, upgrade_key) DO UPDATE SET level = level + 1
                """,
                (str(player.user_id), key),
            )
            await conn.execute(
                """
                INSERT INTO economy_log (user_id, delta, reason, balance_after)
                VALUES (?, ?, ?, ?)
                """,
                (str(player.user_id), -cost, f"upgrade:{key}", player.balance - cost),
            )
            new_level = current + 1

        player.balance -= cost
        player.upgrades[key] = new_level
        self.log.info("user=%s upgraded %s -> level %d (-%d coins)", player.user_id, key, new_level, cost)
        return new_level, cost
