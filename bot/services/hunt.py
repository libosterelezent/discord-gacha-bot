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

from sqlalchemy import text

from bot.core.decorators import timed
from bot.core.events import GameEvent
from bot.core.exceptions import GachaBotError
from bot.core.util import (
    SQL_ADD_BALANCE,
    SQL_INSERT_ECO_LOG,
    SQL_INSERT_EQUIPMENT,
    SQL_UPSERT_INVENTORY,
    now_iso,
)
from bot.models.items import Equipment
from bot.models.player import Player, StatProfile
from bot.services.base import BaseService

if TYPE_CHECKING:
    from bot.config import GameSettings
    from bot.content.registry import ContentRegistry, EncounterChoice, EncounterSpec, HuntModifier
    from bot.core.cooldowns import CooldownManager
    from bot.core.database import Database
    from bot.core.events import EventBus
    from bot.models.items import HuntEnemy


@dataclass(slots=True)
class HuntResult:
    enemy: "HuntEnemy | None"
    success: bool
    coins: int
    xp: int
    power: int
    enemy_power: int
    drops: list[Equipment] = field(default_factory=list)
    card_key: str | None = None
    level_up: int | None = None
    modifier: "HuntModifier | None" = None
    encounter: "EncounterSpec | None" = None

    @property
    def headline(self) -> str:
        if self.encounter is not None:
            return f"{self.encounter.emoji} Something unusual happened..."
        if self.success and self.enemy is not None:
            return f"\u2694\ufe0f You defeated **{self.enemy.name}** {self.enemy.rarity.emoji}"
        if self.enemy is not None:
            return f"\U0001f480 **{self.enemy.name}** {self.enemy.rarity.emoji} fought back and you fled..."
        return "\u2753 The hunt ended strangely."


@dataclass(slots=True)
class EncounterResolution:
    """Outcome of a chosen encounter option."""

    encounter: "EncounterSpec"
    choice: "EncounterChoice"
    coins: int
    shards: int
    xp: int
    item: Equipment | None = None
    level_up: int | None = None


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

    def todays_modifier(self):
        """The daily hunt mutator (deterministic per UTC date)."""
        from datetime import date

        return self.content.modifier_for_date(date.today())

    def _pick_enemy(self, profile: StatProfile, tier_bonus: int = 0) -> "HuntEnemy":
        """Rarity roll tilted by luck, capped by the player's power."""
        cap_tier = 1 + profile.power // self.settings.hunt.tier_cap_divisor + tier_bonus
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

    def _roll_equipment_drop(self, enemy: "HuntEnemy", profile: StatProfile, drop_mult: float = 1.0) -> Equipment | None:
        hunt = self.settings.hunt
        base = hunt.equipment_drop_base + hunt.equipment_drop_per_tier * enemy.rarity.tier
        if self._rng.random() > min(0.9, (base + profile.luck * hunt.equipment_drop_luck_scale) * drop_mult):
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
        # rare encounter? it replaces the hunt entirely (cooldown already spent)
        if self._rng.random() < self.content.encounter_chance:
            encounter = self.content.pick_encounter(self._rng)
            if encounter is not None:
                return HuntResult(
                    enemy=None, success=False, coins=0, xp=0,
                    power=profile.power, enemy_power=0, encounter=encounter,
                )

        modifier = self.todays_modifier()
        if modifier is not None:
            # apply the daily luck scaling to a copy of the profile
            import dataclasses

            profile = dataclasses.replace(
                profile, luck=min(profile.luck * modifier.luck_scale, 1.5)
            )

        enemy = self._pick_enemy(profile, tier_bonus=modifier.tier_bonus if modifier else 0)
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
            drop = self._roll_equipment_drop(
                enemy, profile, drop_mult=modifier.drop_mult if modifier else 1.0
            )
            if drop is not None:
                drops.append(drop)
            card_key = self._roll_card_drop(enemy, profile)
        else:
            coin_reward = int(enemy.base_coins * 0.2 * profile.coin_multiplier)
            xp_reward = max(1, int(enemy.base_xp * 0.25))

        if modifier is not None:
            coin_reward = int(coin_reward * modifier.coin_mult)
            xp_reward = max(1, int(xp_reward * modifier.xp_mult))

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
            card_key=card_key, level_up=level_up, modifier=modifier,
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

    # -- rare encounters ---------------------------------------------------------

    async def resolve_encounter(
        self, guild_id: int | None, player: Player, profile: StatProfile,
        encounter: "EncounterSpec", choice_key: str,
    ) -> EncounterResolution:
        """Apply a chosen encounter outcome (coins/shards/XP/item)."""
        choice = encounter.choice(choice_key)
        if choice is None:
            raise GachaBotError("That choice is no longer available.")

        coins = int(self._rng.randint(*choice.coins) * (profile.coin_multiplier if choice.coins[1] > 0 else 1.0))
        shards = self._rng.randint(*choice.shards)
        xp = self._rng.randint(*choice.xp)
        item: Equipment | None = None
        if choice.item_chance > 0 and self._rng.random() < choice.item_chance:
            rarity = self.content.roll_rarity(self._rng, luck=profile.luck)
            templates = self.content.equipment_by_rarity(rarity)
            if templates:
                item = self._rng.choice(templates).roll(self._rng, rarity)

        now = now_iso()
        async with self.db.transaction() as conn:
            if coins:
                await conn.execute(
                    SQL_ADD_BALANCE,
                    {"d": coins, "t": now, "g": player.guild_id, "u": player.user_id},
                )
                await conn.execute(
                    SQL_INSERT_ECO_LOG,
                    {"g": player.guild_id, "u": player.user_id, "d": coins,
                     "r": f"encounter:{encounter.key}", "b": player.balance + coins, "t": now},
                )
            if shards:
                await conn.execute(
                    text("UPDATE players SET shards = shards + :s, updated_at = :t WHERE guild_id = :g AND user_id = :u"),
                    {"s": shards, "t": now, "g": player.guild_id, "u": player.user_id},
                )
            if item is not None:
                await conn.execute(
                    SQL_INSERT_EQUIPMENT,
                    {"g": player.guild_id, "u": player.user_id, "k": item.key,
                     "s": item.etype.key, "r": item.rarity.key,
                     "a": item.attack, "d": item.defense, "l": item.luck, "t": now},
                )
        player.balance += max(coins, 0)
        player.shards += shards
        level_up = await self.economy.add_xp_and_level(player, xp) if xp else None

        self.log.info(
            "user=%s encounter %s choice=%s coins=%+d shards=%+d xp=%+d item=%s",
            player.user_id, encounter.key, choice_key, coins, shards, xp, item is not None,
        )
        await self.bus.publish(
            GameEvent(
                category="hunt", action="encounter", guild_id=guild_id, user_id=player.user_id,
                message=f"encounter: {encounter.name} — chose {choice.label} "
                        f"({coins:+,} coins, +{shards} shards, +{xp} XP)",
            )
        )
        return EncounterResolution(
            encounter=encounter, choice=choice, coins=coins, shards=shards, xp=xp,
            item=item, level_up=level_up,
        )
