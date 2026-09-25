"""Upgrade service — the catalog lives in content JSON (``upgrades.json``).

Buying levels, cost curves and effect aggregation are identical for
every spec; adding a new upgrade is a JSON entry, nothing else.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from sqlalchemy import text

from bot.core.events import GameEvent
from bot.core.exceptions import InsufficientFundsError, UpgradeError
from bot.models.player import Player
from bot.services.base import BaseService

if TYPE_CHECKING:
    from bot.content.registry import ContentRegistry, UpgradeSpec
    from bot.core.cooldowns import CooldownManager
    from bot.core.database import Database
    from bot.core.events import EventBus
    from bot.config import GameSettings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UpgradeService(BaseService):
    log_name = "gacha.upgrades"

    def __init__(self, db: "Database", content: "ContentRegistry", settings: "GameSettings",
                 bus: "EventBus", cooldowns: "CooldownManager", get_player: Callable) -> None:
        super().__init__(db, content, settings, bus, cooldowns)
        self._get_player = get_player  # dependency injected from EconomyService

    async def on_start(self) -> None:
        self.log.info("Upgrade service ready (%d upgrades)", len(self.content.all_upgrades()))

    async def levels_for(self, guild_id: int, user_id: int) -> dict[str, int]:
        rows = await self.db.fetch_all(
            "SELECT upgrade_key, level FROM upgrades WHERE guild_id = :g AND user_id = :u",
            {"g": guild_id, "u": user_id},
        )
        return {r["upgrade_key"]: r["level"] for r in rows}

    async def buy(self, guild_id: int | None, player: Player, key: str) -> tuple["UpgradeSpec", int]:
        """Purchase one level; returns (spec, new_level)."""
        spec = self.content.upgrade(key)
        if spec is None:
            raise UpgradeError(f"Unknown upgrade `{key}`.", upgrade_key=key)
        current = (await self.levels_for(player.guild_id, player.user_id)).get(key, 0)
        if current >= spec.max_level:
            raise UpgradeError(
                f"`{spec.name}` is already maxed (level {current}/{spec.max_level}).",
                upgrade_key=key, level=current,
            )
        cost = spec.cost(current)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)

        now = _now_iso()
        async with self.db.transaction() as conn:
            await conn.execute(
                text("UPDATE players SET balance = balance - :c, updated_at = :t WHERE guild_id = :g AND user_id = :u"),
                {"c": cost, "t": now, "g": player.guild_id, "u": player.user_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO upgrades (guild_id, user_id, upgrade_key, level) VALUES (:g, :u, :k, 1)
                    ON CONFLICT (guild_id, user_id, upgrade_key) DO UPDATE SET level = level + 1
                    """
                ),
                {"g": player.guild_id, "u": player.user_id, "k": key},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO economy_log (guild_id, user_id, delta, reason, balance_after, created_at)
                    VALUES (:g, :u, :d, :r, :b, :t)
                    """
                ),
                {"g": player.guild_id, "u": player.user_id, "d": -cost, "r": f"upgrade:{key}", "b": player.balance - cost, "t": now},
            )
            new_level = current + 1

        player.balance -= cost
        player.upgrades[key] = new_level
        self.log.info("user=%s upgraded %s -> level %d (-%d coins)", player.user_id, key, new_level, cost)
        await self.bus.publish(
            GameEvent(
                category="upgrades", action="buy", guild_id=guild_id, user_id=player.user_id,
                message=f"{spec.name} \u2192 level {new_level} (-{cost:,})",
            )
        )
        return spec, new_level
