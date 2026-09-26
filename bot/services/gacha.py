"""Gacha service: weighted pulls, pity system, duplicates -> shards.

Pity design (all knobs in ``config/game.json``)
------------------------------------------------
* Every pull increments ``pity_counter``.
* At ``gacha.pity_limit`` the pull is *forced* to the ``pity_floor``
  rarity (or one tier higher with ``pity_floor_upgrade_chance``) and
  the counter resets (hard pity).
* After ``gacha.soft_pity`` the floor rarity weights ramp linearly by
  ``gacha.soft_ramp`` (soft pity) — mirrors modern gacha games.

Rarity rolling is delegated to the content registry's pluggable spawn
algorithm, so retuning or adding tiers never touches this service.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text

from bot.core.decorators import timed
from bot.core.events import GameEvent
from bot.core.exceptions import GachaError, InsufficientFundsError
from bot.core.util import SQL_INSERT_EQUIPMENT, SQL_UPSERT_INVENTORY, now_iso
from bot.models.items import Equipment
from bot.models.player import Player
from bot.services.base import BaseService

if TYPE_CHECKING:
    from bot.content.registry import RarityTier
    from bot.models.items import GachaCard


@dataclass(slots=True)
class PullOutcome:
    """Result of a single pull — a tagged union of possibilities."""

    rarity: "RarityTier"
    card: "GachaCard | None" = None
    equipment: Equipment | None = None
    is_new_card: bool = False
    shards_awarded: int = 0
    pity_triggered: bool = False
    roll_pct: float | None = None     # equipment stat-roll percentile

    @property
    def kind(self) -> str:
        return "card" if self.card else "equipment"

    def describe(self) -> str:
        shard_emoji = "\u2728"
        if self.card:
            tag = "NEW!" if self.is_new_card else f"+{self.shards_awarded}{shard_emoji}"
            return f"{self.rarity.emoji} **{self.card.name}** ({self.rarity.stars}) {tag}"
        assert self.equipment is not None
        roll = ""
        if self.roll_pct is not None and self.roll_pct >= 0.95:
            roll = f" \U0001f525 **GOD ROLL** ({self.roll_pct * 100:.0f}%)"
        elif self.roll_pct is not None and self.roll_pct >= 0.9:
            roll = f" ({self.roll_pct * 100:.0f}% roll)"
        return f"{self.rarity.emoji} **{self.equipment.name}** ({self.rarity.stars}){roll}"


@dataclass(slots=True)
class PullSession:
    """Aggregated results of one pull invocation (1x or 10x)."""

    outcomes: list[PullOutcome] = field(default_factory=list)
    cost: int = 0
    new_cards: int = 0
    shards_gained: int = 0
    best: "RarityTier | None" = None

    def add(self, outcome: PullOutcome) -> None:
        self.outcomes.append(outcome)
        self.new_cards += int(outcome.is_new_card)
        self.shards_gained += outcome.shards_awarded
        if self.best is None or outcome.rarity.tier > self.best.tier:
            self.best = outcome.rarity


_SQL_UPSERT_INVENTORY = SQL_UPSERT_INVENTORY
_SQL_INSERT_EQUIPMENT = SQL_INSERT_EQUIPMENT
_SQL_APPLY_PULL = text(
    """
    UPDATE players
    SET total_pulls = total_pulls + :n,
        pity_counter = CASE WHEN :reset = 1 THEN :residual ELSE pity_counter + :n END,
        shards = shards + :sh_delta,
        balance = balance - :cost,
        updated_at = :t
    WHERE guild_id = :g AND user_id = :u
      AND balance >= :cost
      AND shards + :sh_delta >= 0
    """
)


class GachaService(BaseService):
    log_name = "gacha.gacha"

    def _post_init(self) -> None:
        self._rng = random.Random()

    async def on_start(self) -> None:
        self.log.info(
            "Gacha service ready: %d cards, %d rarity tiers, pity=%d",
            len(self.content.all_cards()), len(self.content.rarities),
            self.settings.gacha.pity_limit,
        )

    # -- internal rolling ------------------------------------------------------

    def _roll_rarity(self, luck: float, pity_counter: int) -> tuple["RarityTier", bool]:
        """Return (rarity, pity_triggered)."""
        gacha = self.settings.gacha
        if pity_counter + 1 >= gacha.pity_limit:
            floor = self.content.rarity(gacha.pity_floor) or self.content.highest_tier
            forced = floor
            above = self.content.tier(floor.tier + 1)
            if above is not None and self._rng.random() < gacha.pity_floor_upgrade_chance:
                forced = above
            return forced, True

        overrides: dict[str, float] | None = None
        if pity_counter + 1 > gacha.soft_pity:
            floor = self.content.rarity(gacha.pity_floor) or self.content.highest_tier
            ramp = (pity_counter + 1 - gacha.soft_pity) * gacha.soft_ramp
            overrides = {floor.key: floor.weight + ramp}
            above = self.content.tier(floor.tier + 1)
            if above is not None:
                overrides[above.key] = above.weight + ramp / 6

        rolled = self.content.roll_rarity(self._rng, luck=luck, weight_overrides=overrides)
        # luck tilt: one reroll kept if higher (no pity manipulation)
        if luck > 0 and self._rng.random() < luck:
            reroll = self.content.roll_rarity(self._rng, luck=luck, weight_overrides=overrides)
            if reroll.tier > rolled.tier:
                rolled = reroll
        return rolled, False

    def _roll_outcome(self, rarity: "RarityTier", owned_cards: set[str]) -> PullOutcome:
        """Cards vs equipment split at the rolled rarity (chance from settings)."""
        if self._rng.random() < self.settings.gacha.card_chance:
            cards = self.content.cards_by_rarity(rarity) or self.content.all_cards()
            card = self._rng.choice(cards)
            if card.key in owned_cards:
                return PullOutcome(rarity=rarity, card=card, shards_awarded=card.rarity.shard_reward)
            return PullOutcome(rarity=rarity, card=card, is_new_card=True)

        templates = self.content.equipment_by_rarity(rarity)
        if not templates:
            cards = self.content.cards_by_rarity(rarity) or self.content.all_cards()
            card = self._rng.choice(cards)
            return PullOutcome(rarity=rarity, card=card, is_new_card=card.key not in owned_cards)
        template = self._rng.choice(templates)
        equipment = template.roll(self._rng, rarity)
        return PullOutcome(
            rarity=rarity, equipment=equipment,
            roll_pct=self.content.equipment_roll_percentile(equipment),
        )

    # -- public API ------------------------------------------------------------

    async def owned_card_keys(self, guild_id: int, user_id: int) -> set[str]:
        rows = await self.db.fetch_all(
            "SELECT item_key FROM inventory WHERE guild_id = :g AND user_id = :u",
            {"g": guild_id, "u": user_id},
        )
        return {r["item_key"] for r in rows}

    @timed()
    async def pull(self, guild_id: int | None, player: Player, count: int | None = None, use_shards: bool = False) -> PullSession:
        """Run a pull session. `count` 1..multi; shards pay at a discount."""
        gacha = self.settings.gacha
        count = count or 1
        if count not in (1, gacha.multi_count):
            raise GachaError(f"Pull count must be 1 or {gacha.multi_count}.")

        cost = gacha.multi_cost if count == gacha.multi_count else gacha.pull_cost
        shard_cost = int(cost / gacha.shard_pull_divisor)
        if use_shards:
            if player.shards < shard_cost:
                raise InsufficientFundsError(shard_cost, player.shards)
        elif player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)

        # modular sliding-window cooldown (maintainer-configurable)
        self.cooldowns.check_window(player.guild_id, player.user_id, "pull")

        now = now_iso()
        owned = await self.owned_card_keys(player.guild_id, player.user_id)
        session = PullSession(cost=cost)

        async with self.db.transaction() as conn:
            for _ in range(count):
                rarity, pity_hit = self._roll_rarity(0.0, player.pity_counter)
                outcome = self._roll_outcome(rarity, owned)
                outcome.pity_triggered = pity_hit

                player.pity_counter = 0 if pity_hit else player.pity_counter + 1
                player.total_pulls += 1

                if outcome.card and outcome.is_new_card:
                    owned.add(outcome.card.key)
                    await conn.execute(
                        _SQL_UPSERT_INVENTORY, {"g": player.guild_id, "u": player.user_id, "k": outcome.card.key}
                    )
                elif outcome.card:  # duplicate
                    player.shards += outcome.shards_awarded
                elif outcome.equipment is not None:
                    equip = outcome.equipment
                    equip_id = (
                        await conn.execute(
                            _SQL_INSERT_EQUIPMENT,
                            {
                                "g": player.guild_id, "u": player.user_id, "k": equip.key,
                                "s": equip.etype.key, "r": equip.rarity.key,
                                "a": equip.attack, "d": equip.defense, "l": equip.luck, "t": now,
                            },
                        )
                    ).scalar_one()
                    equip.db_id = int(equip_id)

                session.add(outcome)

            # persist counters & payment atomically: all deltas are relative
            # and guarded so concurrent commands cannot corrupt state
            shards_gained = session.shards_gained
            sh_delta = shards_gained - shard_cost if use_shards else shards_gained
            pay_cost = 0 if use_shards else cost
            reset = any(o.pity_triggered for o in session.outcomes)
            residual = player.pity_counter  # already post-loop value
            result = await conn.execute(
                _SQL_APPLY_PULL,
                {
                    "n": count, "reset": int(reset), "residual": residual,
                    "sh_delta": sh_delta, "cost": pay_cost, "t": now,
                    "g": player.guild_id, "u": player.user_id,
                },
            )
            if result.rowcount == 0:
                # guard tripped: funds moved elsewhere since the snapshot
                if use_shards:
                    raise InsufficientFundsError(shard_cost, player.shards)
                raise InsufficientFundsError(cost, player.balance)
            player.total_pulls += count
            player.shards += sh_delta
            player.balance -= pay_cost

        self.log.info(
            "user=%s pulled x%d (cost=%d, shards=%s) best=%s new=%d",
            player.user_id, count, cost, use_shards,
            session.best.label if session.best else "-", session.new_cards,
        )
        await self.bus.publish(
            GameEvent(
                category="gacha", action="pull", guild_id=guild_id, user_id=player.user_id,
                message=f"x{count} pull — best: {session.best.label if session.best else '-'}"
                        f" (+{session.new_cards} new, +{session.shards_gained} shards)",
                colour=session.best.colour if session.best else None,
                fields={"pity": any(o.pity_triggered for o in session.outcomes)},
            )
        )
        return session

    async def collection(self, guild_id: int, user_id: int) -> list[tuple["GachaCard", int]]:
        rows = await self.db.fetch_all(
            """
            SELECT item_key, quantity FROM inventory
            WHERE guild_id = :g AND user_id = :u ORDER BY quantity DESC
            """,
            {"g": guild_id, "u": user_id},
        )
        result = []
        for r in rows:
            card = self.content.card(r["item_key"])
            if card:
                result.append((card, r["quantity"]))
        return result

    async def collection_progress(self, guild_id: int, user_id: int) -> tuple[int, int]:
        owned = await self.owned_card_keys(guild_id, user_id)
        return len(owned), len(self.content.all_cards())

    async def sell_duplicates(self, guild_id: int | None, player: Player) -> int:
        """Convert spare copies (quantity > 1) into coins. Returns coins gained."""
        rows = await self.db.fetch_all(
            """
            SELECT item_key, quantity FROM inventory
            WHERE guild_id = :g AND user_id = :u AND quantity > 1
            """,
            {"g": player.guild_id, "u": player.user_id},
        )
        total = 0
        now = now_iso()
        async with self.db.transaction() as conn:
            for row in rows:
                card = self.content.card(row["item_key"])
                if card is None:
                    continue
                dupes = row["quantity"] - 1
                total += dupes * card.sell_value
                await conn.execute(
                    text("UPDATE inventory SET quantity = 1 WHERE guild_id = :g AND user_id = :u AND item_key = :k"),
                    {"g": player.guild_id, "u": player.user_id, "k": row["item_key"]},
                )
            if total:
                await conn.execute(
                    text("UPDATE players SET balance = balance + :b, updated_at = :t WHERE guild_id = :g AND user_id = :u"),
                    {"b": total, "t": now, "g": player.guild_id, "u": player.user_id},
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO economy_log (guild_id, user_id, delta, reason, balance_after, created_at)
                        VALUES (:g, :u, :d, :r, :b, :t)
                        """
                    ),
                    {"g": player.guild_id, "u": player.user_id, "d": total, "r": "sell_duplicates", "b": player.balance + total, "t": now},
                )
        player.balance += total
        if total:
            await self.bus.publish(
                GameEvent(
                    category="gacha", action="sell_dupes", guild_id=guild_id, user_id=player.user_id,
                    message=f"sold duplicates for {total:,}",
                )
            )
        return total
