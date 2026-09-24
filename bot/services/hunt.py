"""Hunt service: combat rolls, loot generation, cooldowns.

Hunt flow
---------
1. Cooldown check (scaled by Swiftness upgrade).
2. Enemy rarity roll tilted by luck; pick enemy of that rarity.
3. Success check: player power vs enemy tier + luck jitter.
4. Rewards: coins (Greed/level multipliers), XP, equipment drop chance,
   rare card drop chance for high-tier kills.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from bot.config import CONFIG
from bot.core.database import Database
from bot.core.decorators import timed
from bot.core.exceptions import CooldownError
from bot.models.game_data import load_game_data
from bot.models.items import Equipment, HuntEnemy, ItemRegistry
from bot.models.player import Player, StatProfile
from bot.models.rarities import Rarity
from bot.services.base import BaseService
from bot.services.economy import EconomyService, _cooldown, _set_cooldown

load_game_data()


@dataclass(slots=True)
class HuntResult:
    enemy: HuntEnemy
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


class HuntService(BaseService):
    log_name = "gacha.hunt"

    def __init__(self, db: Database, economy: EconomyService, rng: random.Random | None = None) -> None:
        self.economy = economy
        self._rng = rng or random.Random()
        super().__init__(db)

    async def on_start(self) -> None:
        self.log.info("Hunt service ready (%d enemies)", len(ItemRegistry.all_enemies()))

    # -- helpers -----------------------------------------------------------------

    def cooldown_remaining(self, player: Player, profile: StatProfile) -> float:
        return _cooldown(player.user_id, "hunt")

    def _effective_cooldown(self, profile: StatProfile) -> float:
        return CONFIG.hunt_cooldown_seconds * profile.cooldown_multiplier

    def _pick_enemy(self, profile: StatProfile) -> HuntEnemy:
        """Rarity roll tilted by luck, weighted towards reachability."""
        tier_cap = min(Rarity.MYTHIC, Rarity(1 + profile.power // 60))
        candidates = {r: Rarity.weights()[r] for r in Rarity if r <= tier_cap}
        members = list(candidates)
        weights = [candidates[r] * (1 + profile.luck) ** (r.value - 1) for r in members]
        rarity = self._rng.choices(members, weights=weights, k=1)[0]
        pool = ItemRegistry.enemies_by_rarity(rarity)
        return self._rng.choice(pool)

    def _enemy_power(self, enemy: HuntEnemy) -> int:
        return 25 * enemy.rarity.value ** 2  # 25..900

    def _roll_equipment_drop(self, enemy: HuntEnemy, profile: StatProfile) -> Equipment | None:
        base_chance = 0.06 + 0.02 * enemy.rarity.value
        if self._rng.random() > base_chance + profile.luck * 0.15:
            return None
        templates = [t for t in ItemRegistry.all_equipment() if t.min_rarity <= enemy.rarity]
        if not templates:
            return None
        template = self._rng.choice(templates)
        return template.roll(self._rng, enemy.rarity)

    def _roll_card_drop(self, enemy: HuntEnemy, profile: StatProfile) -> str | None:
        chance = 0.015 * enemy.rarity.value + profile.luck * 0.05
        if self._rng.random() > chance:
            return None
        pool = ItemRegistry.cards_by_rarity(enemy.rarity)
        return self._rng.choice(pool).key if pool else None

    # -- core -------------------------------------------------------------------

    @timed()
    async def hunt(self, player: Player, profile: StatProfile) -> HuntResult:
        remaining = _cooldown(player.user_id, "hunt")
        if remaining > 0:
            raise CooldownError(remaining, unit="seconds")

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
            xp_reward = int(enemy.base_xp * self._rng.uniform(0.9, 1.15))
            if (drop := self._roll_equipment_drop(enemy, profile)) is not None:
                drops.append(drop)
            card_key = self._roll_card_drop(enemy, profile)
        else:
            coin_reward = int(enemy.base_coins * 0.2 * profile.coin_multiplier)
            xp_reward = max(1, int(enemy.base_xp * 0.25))

        # persist rewards
        await self.economy._apply_delta(player.user_id, coin_reward, f"hunt:{enemy.key}")

        async with self.db.transaction() as conn:
            for item in drops:
                cur = await conn.execute(
                    """
                    INSERT INTO equipment (user_id, item_key, slot, rarity, level, attack, defense, luck)
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        str(player.user_id), item.key, item.etype.key, item.rarity.key,
                        item.attack, item.defense, item.luck,
                    ),
                )
                item.db_id = cur.lastrowid
            if card_key:
                await conn.execute(
                    """
                    INSERT INTO inventory (user_id, item_key, quantity) VALUES (?, ?, 1)
                    ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = quantity + 1
                    """,
                    (str(player.user_id), card_key),
                )

        _set_cooldown(player.user_id, "hunt", self._effective_cooldown(profile))
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
        return result
