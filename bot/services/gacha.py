"""Gacha service: weighted pulls, pity system, duplicates -> shards.

Pity design
-----------
* Every pull increments ``pity_counter``.
* At ``CONFIG.gacha_pity_limit`` the pull is *forced* LEGENDARY+ and the
  counter resets (hard pity).
* After ``CONFIG.gacha_soft_pity`` the Legendary/Mythic weights ramp
  linearly (soft pity) — mirrors modern gacha games.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from bot.config import CONFIG, CONSTANTS
from bot.core.database import Database
from bot.core.decorators import timed
from bot.core.exceptions import GachaError, InsufficientFundsError
from bot.models.game_data import load_game_data
from bot.models.items import Equipment, GachaCard, ItemRegistry
from bot.models.player import Player
from bot.models.rarities import Rarity
from bot.services.base import BaseService

load_game_data()


@dataclass(slots=True)
class PullOutcome:
    """Result of a single pull — a tagged union of possibilities."""

    rarity: Rarity
    card: GachaCard | None = None
    equipment: Equipment | None = None
    is_new_card: bool = False
    shards_awarded: int = 0
    pity_triggered: bool = False

    @property
    def kind(self) -> str:
        return "card" if self.card else "equipment"

    def describe(self) -> str:
        if self.card:
            tag = "NEW!" if self.is_new_card else f"+{self.shards_awarded}{CONSTANTS.shard_emoji}"
            return f"{self.rarity.emoji} **{self.card.name}** ({self.rarity.stars}) {tag}"
        assert self.equipment is not None
        return f"{self.rarity.emoji} **{self.equipment.name}** ({self.rarity.stars})"


@dataclass(slots=True)
class PullSession:
    """Aggregated results of one /pull invocation (1x or 10x)."""

    outcomes: list[PullOutcome] = field(default_factory=list)
    cost: int = 0
    new_cards: int = 0
    shards_gained: int = 0
    best: Rarity = Rarity.COMMON

    def add(self, outcome: PullOutcome) -> None:
        self.outcomes.append(outcome)
        self.new_cards += int(outcome.is_new_card)
        self.shards_gained += outcome.shards_awarded
        if outcome.rarity > self.best:
            self.best = outcome.rarity


class GachaService(BaseService):
    log_name = "gacha.gacha"

    def __init__(self, db: Database, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._card_keys_by_rarity: dict[Rarity, list[GachaCard]] = {}
        self._equip_by_max_rarity: dict[Rarity, list] = {}
        super().__init__(db)

    def _post_init(self) -> None:
        load_game_data()
        for rarity in Rarity:
            self._card_keys_by_rarity[rarity] = ItemRegistry.cards_by_rarity(rarity)

    async def on_start(self) -> None:
        self.log.info(
            "Gacha service ready: %d cards, pity=%d, soft=%d",
            len(ItemRegistry.all_cards()), CONFIG.gacha_pity_limit, CONFIG.gacha_soft_pity,
        )

    # -- internal rolling ------------------------------------------------------

    def _roll_rarity(self, luck: float, pity_counter: int) -> tuple[Rarity, bool]:
        """Return (rarity, pity_triggered)."""
        if pity_counter + 1 >= CONFIG.gacha_pity_limit:
            forced = Rarity.LEGENDARY if self._rng.random() < 0.9 else Rarity.MYTHIC
            return forced, True
        weights = Rarity.weights()
        if pity_counter + 1 > CONFIG.gacha_soft_pity:
            ramp = (pity_counter + 1 - CONFIG.gacha_soft_pity) * 8.0
            weights[Rarity.LEGENDARY] += ramp
            weights[Rarity.MYTHIC] += ramp / 6
        members = list(Rarity)
        rolled = self._rng.choices(members, weights=[weights[r] for r in members], k=1)[0]
        # luck tilt: one reroll kept if higher (no pity manipulation)
        if luck > 0 and self._rng.random() < luck:
            reroll = self._rng.choices(members, weights=[weights[r] for r in members], k=1)[0]
            rolled = max(rolled, reroll)
        return rolled, False

    def _roll_outcome(self, rarity: Rarity, owned_cards: set[str], luck: float) -> PullOutcome:
        """Cards 60% / equipment 40% split at the rolled rarity."""
        if self._rng.random() < 0.6:
            cards = self._card_keys_by_rarity.get(rarity) or ItemRegistry.all_cards()
            card = self._rng.choice(cards)
            if card.key in owned_cards:
                shards = CONSTANTS.duplicate_shard_reward.get(card.rarity.key, 1)
                return PullOutcome(rarity=rarity, card=card, shards_awarded=shards)
            return PullOutcome(rarity=rarity, card=card, is_new_card=True)

        templates = [t for t in ItemRegistry.all_equipment() if t.min_rarity <= rarity]
        if not templates:
            cards = self._card_keys_by_rarity.get(rarity) or ItemRegistry.all_cards()
            card = self._rng.choice(cards)
            return PullOutcome(rarity=rarity, card=card, is_new_card=card.key not in owned_cards)
        template = self._rng.choice(templates)
        equipment = template.roll(self._rng, rarity)
        return PullOutcome(rarity=rarity, equipment=equipment)

    # -- public API ------------------------------------------------------------

    async def owned_card_keys(self, user_id: int) -> set[str]:
        rows = await self.db.fetch_all(
            "SELECT item_key FROM inventory WHERE user_id = ?", (str(user_id),)
        )
        return {r["item_key"] for r in rows}

    @timed()
    async def pull(self, player: Player, count: int = 1, use_shards: bool = False) -> PullSession:
        """Run a pull session. `count` in {1, 10}; shards pay with a 25% discount."""
        if count not in (1, 10):
            raise GachaError("Pull count must be 1 or 10.")
        cost = CONFIG.gacha_multi_cost if count == 10 else CONFIG.gacha_pull_cost
        shard_cost = int(cost / 10)  # 10 shards per pull equivalent
        if use_shards:
            if player.shards < shard_cost:
                raise InsufficientFundsError(shard_cost, player.shards)
        elif player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)

        owned = await self.owned_card_keys(player.user_id)
        session = PullSession(cost=cost)

        async with self.db.transaction() as conn:
            for _ in range(count):
                rarity, pity_hit = self._roll_rarity(0.0, player.pity_counter)
                outcome = self._roll_outcome(rarity, owned, 0.0)
                outcome.pity_triggered = pity_hit

                player.pity_counter = 0 if pity_hit else player.pity_counter + 1
                player.total_pulls += 1

                if outcome.card and outcome.is_new_card:
                    owned.add(outcome.card.key)
                    await conn.execute(
                        """
                        INSERT INTO inventory (user_id, item_key, quantity) VALUES (?, ?, 1)
                        ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = quantity + 1
                        """,
                        (str(player.user_id), outcome.card.key),
                    )
                elif outcome.card:  # duplicate
                    player.shards += outcome.shards_awarded
                elif outcome.equipment is not None:
                    cur = await conn.execute(
                        """
                        INSERT INTO equipment (user_id, item_key, slot, rarity, level, attack, defense, luck)
                        VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                        """,
                        (
                            str(player.user_id), outcome.equipment.key,
                            outcome.equipment.etype.key, outcome.equipment.rarity.key,
                            outcome.equipment.attack, outcome.equipment.defense, outcome.equipment.luck,
                        ),
                    )
                    outcome.equipment.db_id = cur.lastrowid

                session.add(outcome)

            # persist counters & payment in the same transaction
            await conn.execute(
                """
                UPDATE players
                SET total_pulls = ?, pity_counter = ?, shards = ?,
                    balance = CASE WHEN ? THEN balance ELSE balance - ? END,
                    updated_at = datetime('now')
                WHERE user_id = ?
                """,
                (
                    player.total_pulls, player.pity_counter, player.shards,
                    1 if use_shards else 0, cost, str(player.user_id),
                ),
            )
            if use_shards:
                await conn.execute(
                    "UPDATE players SET shards = shards - ? WHERE user_id = ?",
                    (shard_cost, str(player.user_id)),
                )
                player.shards -= shard_cost
            else:
                player.balance -= cost

        session.best = max((o.rarity for o in session.outcomes), default=Rarity.COMMON)
        self.log.info(
            "user=%s pulled x%d (cost=%d, shards=%s) best=%s new=%d",
            player.user_id, count, cost, use_shards, session.best.name, session.new_cards,
        )
        return session

    async def collection(self, user_id: int) -> list[tuple[GachaCard, int]]:
        rows = await self.db.fetch_all(
            "SELECT item_key, quantity FROM inventory WHERE user_id = ? ORDER BY quantity DESC",
            (str(user_id),),
        )
        result = []
        for r in rows:
            card = ItemRegistry.card(r["item_key"])
            if card:
                result.append((card, r["quantity"]))
        return result

    async def collection_progress(self, user_id: int) -> tuple[int, int]:
        owned = await self.owned_card_keys(user_id)
        return len(owned), len(ItemRegistry.all_cards())

    async def sell_duplicates(self, player: Player) -> int:
        """Convert spare copies (quantity > 1) into coins. Returns coins gained."""
        rows = await self.db.fetch_all(
            "SELECT item_key, quantity FROM inventory WHERE user_id = ? AND quantity > 1",
            (str(user_id),),
        )
        total = 0
        async with self.db.transaction() as conn:
            for row in rows:
                card = ItemRegistry.card(row["item_key"])
                if card is None:
                    continue
                dupes = row["quantity"] - 1
                gained = dupes * card.sell_value
                total += gained
                await conn.execute(
                    "UPDATE inventory SET quantity = 1 WHERE user_id = ? AND item_key = ?",
                    (str(player.user_id), row["item_key"]),
                )
            if total:
                await conn.execute(
                    "UPDATE players SET balance = balance + ?, updated_at = datetime('now') WHERE user_id = ?",
                    (total, str(player.user_id)),
                )
                await conn.execute(
                    "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                    (str(player.user_id), total, "sell_duplicates", player.balance + total),
                )
        player.balance += total
        return total
