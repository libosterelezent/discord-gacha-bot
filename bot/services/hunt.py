"""Hunt service: combat rolls, loot generation, cooldowns.

Hunt flow
---------
1. Cooldown check (duration from game.json, scaled by Swiftness).
2. Enemy rarity roll via the content registry's spawn algorithm,
   tilted by luck; pick enemy of that rarity.
3. Success check: player power vs enemy tier + luck jitter.
4. Rewards: coins (Greed/level multipliers), XP, equipment drop chance,
   rare card drop chance for high-tier kills.

Enemies, equipment and rarities all come from the content registry —
new content appears in hunts without code changes.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bot.core.decorators import timed
from bot.core.events import GameEvent
from bot.core.util import SQL_INSERT_EQUIPMENT, SQL_UPSERT_INVENTORY, now_iso
from bot.models.items import Equipment
from bot.models.player import Player, StatProfile
from bot.services.base import BaseService

if TYPE_CHECKING:
    from bot.config import GameSettings
    from bot.content.registry import ContentRegistry
    from bot.core.cooldowns import CooldownManager
    from bot.core.database import Database
    from bot.core.events import EventBus
    from bot.models.items import HuntEnemy


@dataclass(slots=True)
class HuntResult:
    enemy: "HuntEnemy"
    success: bool
    coins: int
    xp: int
    power: int
    enemy_power: int
    drops: list[Equipment] = field(default_factory=list)
    card_key: str | None = None
    level_up: int | None = None

    @property
    def headline(self) -> str:
        if self.success:
            return f"\u2694\ufe0f You defeated **{self.enemy.name}** {self.enemy.rarity.emoji}"
        return f"\U0001f480 **{self.enemy.name}** {self.enemy.rarity.emoji} fought back and you fled..."


_SQL_INSERT_EQUIPMENT = SQL_INSERT_EQUIPMENT
_SQL_UPSERT_INVENTORY = SQL_UPSERT_INVENTORY


class HuntService(BaseService):
    log_name = "gacha.hunt"

    def __init__(self, db: "Database", content: "ContentRegistry", settings: "GameSettings",
                 bus: "EventBus", cooldowns: "CooldownManager", economy) -> None:
        super().__init__(db, content, settings, bus, cooldowns)
        self.economy = economy
        self._rng = random.Random()

    async def on_start(self) -> None:
        self.log.info("Hunt service ready (%d enemies)", len(self.content.all_enemies()))

    # -- helpers -----------------------------------------------------------------

    def cooldown_remaining(self, player: Player) -> float:
        return self.cooldowns.remaining(player.guild_id, player.user_id, "hunt")

    def _effective_cooldown(self, profile: StatProfile) -> float:
        return self.settings.cooldown_seconds("hunt") * profile.cooldown_multiplier

    def _pick_enemy(self, profile: StatProfile) -> "HuntEnemy":
        """Rarity roll tilted by luck, capped by the player's power."""
        cap_tier = 1 + profile.power // self.settings.hunt.tier_cap_divisor
        cap = self.content.tier_at_most(cap_tier)
        candidates = [r for r in self.content.rarities if r.tier <= cap.tier]
        weights = [r.weight for r in candidates]
        # luck tilt towards higher tiers within reachable pool
        tilted = [w * (1 + profile.luck) ** (r.tier - 1) for w, r in zip(weights, candidates)]
        rarity = self._rng.choices(candidates, weights=tilted, k=1)[0]
        pool = self.content.enemies_by_rarity(rarity)
        if not pool:  # rarity has no enemies — fall back to any weaker ones
            pool = [e for e in self.content.all_enemies() if e.rarity.tier <= rarity.tier]
        return self._rng.choice(pool)

    def _enemy_power(self, enemy: "HuntEnemy") -> int:
        return 25 * enemy.rarity.tier ** 2  # 25..900 for tiers 1..6

    def _roll_equipment_drop(self, enemy: "HuntEnemy", profile: StatProfile) -> Equipment | None:
        hunt = self.settings.hunt
        base = hunt.equipment_drop_base + hunt.equipment_drop_per_tier * enemy.rarity.tier
        if self._rng.random() > base + profile.luck * hunt.equipment_drop_luck_scale:
            return None
        templates = self.content.equipment_by_rarity(enemy.rarity)
        if not templates:
            return None
        return self._rng.choice(templates).roll(self._rng, enemy.rarity)

    def _roll_card_drop(self, enemy: "HuntEnemy", profile: StatProfile) -> str | None:
        hunt = self.settings.hunt
        chance = hunt.card_drop_per_tier * enemy.rarity.tier + profile.luck * hunt.card_drop_luck_scale
        if self._rng.random() > chance:
            return None
        pool = self.content.cards_by_rarity(enemy.rarity)
        return self._rng.choice(pool).key if pool else None

    # -- core -------------------------------------------------------------------

    @timed()
    async def hunt(self, guild_id: int | None, player: Player, profile: StatProfile) -> HuntResult:
        self.cooldowns.check(player.guild_id, player.user_id, "hunt")
        # reserve the cooldown before side effects: concurrent hunts must
        # not both pass the check and double-credit rewards
        await self.cooldowns.trigger(
            player.guild_id, player.user_id, "hunt", scale=profile.cooldown_multiplier
        )
        try:
            return await self._hunt_locked(guild_id, player, profile)
        except Exception:
            await self.cooldowns.release(player.guild_id, player.user_id, "hunt")
            raise

    async def _hunt_locked(self, guild_id: int | None, player: Player, profile: StatProfile) -> HuntResult:
        enemy = self._pick_enemy(profile)
        e_power = self._enemy_power(enemy)

        # Success chance: sigmoid-ish mapping of power difference + luck.
        ratio = (profile.power + 20) / max(e_power, 1)
        success_chance = min(0.95, max(0.15, ratio / (ratio + 1.6) + profile.luck * 0.1))
        success = self._rng.random() < success_chance

        coin_reward = 0
        xp_reward = 0
        drops: list[Equipment] = []
        card_key: str | None = None

        if success:
            variance = self._rng.uniform(0.85, 1.2)
            coin_reward = int(enemy.base_coins * variance * profile.coin_multiplier)
            xp_reward = int(enemy.base_xp * self._rng.uniform(0.9, 1.15) * profile.xp_multiplier)
            if (drop := self._roll_equipment_drop(enemy, profile)) is not None:
                drops.append(drop)
            card_key = self._roll_card_drop(enemy, profile)
        else:
            coin_reward = int(enemy.base_coins * 0.2 * profile.coin_multiplier)
            xp_reward = max(1, int(enemy.base_xp * 0.25))

        # persist rewards
        await self.economy._apply_delta(guild_id, player.user_id, coin_reward, f"hunt:{enemy.key}")

        now = now_iso()
        async with self.db.transaction() as conn:
            for item in drops:
                equip_id = (
                    await conn.execute(
                        _SQL_INSERT_EQUIPMENT,
                        {
                            "g": player.guild_id, "u": player.user_id, "k": item.key,
                            "s": item.etype.key, "r": item.rarity.key,
                            "a": item.attack, "d": item.defense, "l": item.luck, "t": now,
                        },
                    )
                ).scalar_one()
                item.db_id = int(equip_id)
            if card_key:
                await conn.execute(
                    _SQL_UPSERT_INVENTORY, {"g": player.guild_id, "u": player.user_id, "k": card_key}
                )

        level_up = await self.economy.add_xp_and_level(player, xp_reward)

        result = HuntResult(
            enemy=enemy, success=success, coins=coin_reward, xp=xp_reward,
            power=profile.power, enemy_power=e_power, drops=drops,
            card_key=card_key, level_up=level_up,
        )
        self.log.info(
            "user=%s hunt %s enemy=%s coins=%+d xp=%+d drops=%d",
            player.user_id, "won" if success else "lost", enemy.key, coin_reward, xp_reward, len(drops),
        )
        await self.bus.publish(
            GameEvent(
                category="hunt", action="hunt", guild_id=guild_id, user_id=player.user_id,
                message=f"{'defeated' if success else 'fled from'} {enemy.name} "
                        f"({enemy.rarity.label}) — {coin_reward:+,} coins, +{xp_reward} XP",
                colour=enemy.rarity.colour,
                fields={"success": success, "enemy": enemy.key},
            )
        )
        return result
