"""Huntbot service: an automated hunting companion.

Model
-----
* Purchase once (level 1), then upgrade levels.
* While *active*, every ``huntbot.tick_seconds`` it completes one hunt
  cycle, banking coins (and occasionally equipment) into an *unclaimed*
  pool capped by battery capacity — so owners must collect.
* Offline progress: on boot, elapsed wall-clock is reconciled per bot,
  crediting up to a full battery of ticks that happened while away.

All tuning (costs, tick length, battery, income) lives in game.json.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import text

from bot.core.events import GameEvent
from bot.core.exceptions import HuntBotError, InsufficientFundsError
from bot.core.util import (
    SQL_INSERT_ECO_LOG,
    SQL_INSERT_EQUIPMENT,
    SQL_SUBTRACT_BALANCE_GUARDED,
    now_iso,
)
from bot.models.player import Player
from bot.services.base import BaseService

if TYPE_CHECKING:
    from bot.content.registry import ContentRegistry
    from bot.core.cooldowns import CooldownManager
    from bot.core.database import Database
    from bot.core.events import EventBus
    from bot.config import GameSettings


def _now_iso() -> str:
    return now_iso()


@dataclass(slots=True)
class HuntBotState:
    user_id: int
    guild_id: int
    level: int
    active: bool
    battery: int
    last_tick: datetime | None
    unclaimed_coins: int
    unclaimed_items: list[str]
    hunts_done: int


_SQL_INSERT_BOT = text(
    "INSERT INTO huntbots (guild_id, user_id, level, active, last_tick) VALUES (:g, :u, 1, 1, :t)"
)
_SQL_SPEND = SQL_SUBTRACT_BALANCE_GUARDED
_SQL_ECO_LOG = SQL_INSERT_ECO_LOG
_SQL_INSERT_EQUIPMENT = SQL_INSERT_EQUIPMENT
_SQL_INSERT_ITEM = text(
    "INSERT INTO huntbot_items (guild_id, user_id, item_key) VALUES (:g, :u, :k)"
)
_SQL_TICK_UPDATE = text(
    """
    UPDATE huntbots
    SET unclaimed_coins = unclaimed_coins + :c,
        battery = MIN(battery + 1, :cap), last_tick = :t, hunts_done = hunts_done + 1
    WHERE guild_id = :g AND user_id = :u
    """
)


class HuntBotService(BaseService):
    log_name = "gacha.huntbot"

    def __init__(self, db: "Database", content: "ContentRegistry", settings: "GameSettings",
                 bus: "EventBus", cooldowns: "CooldownManager", rng: random.Random | None = None) -> None:
        super().__init__(db, content, settings, bus, cooldowns)
        self._rng = rng or random.Random()
        self._task: asyncio.Task | None = None

    async def on_start(self) -> None:
        await self._reconcile_offline()
        self._task = asyncio.create_task(self._loop(), name="huntbot-loop")
        self.log.info("Huntbot service ready (background loop started)")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self.log.info("Huntbot loop stopped")

    # -- income model ------------------------------------------------------------

    def income_per_tick(self, level: int) -> int:
        hb = self.settings.huntbot
        return hb.base_income + level * hb.income_per_level

    def efficiency(self, level: int) -> float:
        return 1.0 + level * self.settings.huntbot.efficiency_per_level

    # -- state helpers -------------------------------------------------------------

    async def get_state(self, guild_id: int, user_id: int) -> HuntBotState | None:
        row = await self.db.fetch_one(
            "SELECT * FROM huntbots WHERE guild_id = :g AND user_id = :u",
            {"g": guild_id, "u": user_id},
        )
        if row is None:
            return None
        item_rows = await self.db.fetch_all(
            "SELECT item_key FROM huntbot_items WHERE guild_id = :g AND user_id = :u",
            {"g": guild_id, "u": user_id},
        )
        return HuntBotState(
            user_id=user_id,
            guild_id=guild_id,
            level=row["level"],
            active=bool(row["active"]),
            battery=row["battery"],
            last_tick=datetime.fromisoformat(row["last_tick"]) if row["last_tick"] else None,
            unclaimed_coins=row["unclaimed_coins"],
            unclaimed_items=[r["item_key"] for r in item_rows],
            hunts_done=row["hunts_done"],
        )

    async def require_state(self, guild_id: int, user_id: int) -> HuntBotState:
        state = await self.get_state(guild_id, user_id)
        if state is None:
            raise HuntBotError("You don't own a huntbot yet — buy one with `!huntbot buy`.")
        return state

    # -- ownership / control ----------------------------------------------------------

    def price(self, level: int) -> int:
        """Price of the next level (level 0 == purchase)."""
        hb = self.settings.huntbot
        return int(hb.base_cost * hb.cost_growth ** level)

    async def buy(self, guild_id: int | None, player: Player) -> int:
        state = await self.get_state(player.guild_id, player.user_id)
        if state is not None:
            raise HuntBotError("You already own a huntbot — use `!huntbot upgrade`.")
        cost = self.price(0)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)
        now = _now_iso()
        async with self.db.transaction() as conn:
            spend = await conn.execute(_SQL_SPEND, {"d": cost, "t": now, "g": player.guild_id, "u": player.user_id})
            if spend.rowcount == 0:  # concurrent spend won the funds
                raise InsufficientFundsError(cost, player.balance)
            await conn.execute(_SQL_INSERT_BOT, {"g": player.guild_id, "u": player.user_id, "t": now})
            await conn.execute(
                _SQL_ECO_LOG,
                {"g": player.guild_id, "u": player.user_id, "d": -cost, "r": "huntbot:buy", "b": player.balance - cost, "t": now},
            )
        player.balance -= cost
        self.log.info("user=%s bought huntbot (-%d)", player.user_id, cost)
        await self.bus.publish(
            GameEvent(category="huntbot", action="buy", guild_id=guild_id, user_id=player.user_id,
                      message=f"bought huntbot (-{cost:,})")
        )
        return cost

    async def upgrade(self, guild_id: int | None, player: Player) -> tuple[int, int]:
        state = await self.require_state(player.guild_id, player.user_id)
        cost = self.price(state.level)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)
        now = _now_iso()
        async with self.db.transaction() as conn:
            spend = await conn.execute(_SQL_SPEND, {"d": cost, "t": now, "g": player.guild_id, "u": player.user_id})
            if spend.rowcount == 0:  # concurrent spend won the funds
                raise InsufficientFundsError(cost, player.balance)
            await conn.execute(
                text("UPDATE huntbots SET level = level + 1 WHERE guild_id = :g AND user_id = :u"),
                {"g": player.guild_id, "u": player.user_id},
            )
            await conn.execute(
                _SQL_ECO_LOG,
                {"g": player.guild_id, "u": player.user_id, "d": -cost, "r": "huntbot:upgrade", "b": player.balance - cost, "t": now},
            )
        player.balance -= cost
        self.log.info("user=%s upgraded huntbot -> level %d (-%d)", player.user_id, state.level + 1, cost)
        await self.bus.publish(
            GameEvent(category="huntbot", action="upgrade", guild_id=guild_id, user_id=player.user_id,
                      message=f"upgraded huntbot to level {state.level + 1} (-{cost:,})")
        )
        return state.level + 1, cost

    async def set_active(self, guild_id: int | None, player: Player, active: bool) -> None:
        state = await self.require_state(player.guild_id, player.user_id)
        if state.active == active:
            raise HuntBotError(f"Huntbot is already {'active' if active else 'idle'}.")
        await self.db.execute(
            "UPDATE huntbots SET active = :a, last_tick = :t WHERE guild_id = :g AND user_id = :u",
            {"a": 1 if active else 0, "t": _now_iso(), "g": player.guild_id, "u": player.user_id},
        )
        self.log.info("user=%s huntbot active=%s", player.user_id, active)
        await self.bus.publish(
            GameEvent(category="huntbot", action="toggle", guild_id=guild_id, user_id=player.user_id,
                      message=f"huntbot {'started' if active else 'stopped'}")
        )

    async def collect(self, guild_id: int | None, player: Player, harvest_bonus: float = 0.0) -> tuple[int, list]:
        """Sweep unclaimed rewards into the player's balance/inventory.

        The huntbots row is claimed with an optimistic guard
        (WHERE unclaimed_coins = :seen AND battery = :seen) inside the
        same transaction that credits the player, so two concurrent
        collects cannot both bank the same rewards.
        """
        from bot.models.items import Equipment

        state = await self.require_state(player.guild_id, player.user_id)
        if not state.unclaimed_coins and not state.unclaimed_items and not state.battery:
            raise HuntBotError("Nothing to collect — the battery is still charging.")
        now = _now_iso()
        async with self.db.transaction() as conn:
            claim = (
                await conn.execute(
                    text(
                        """
                        UPDATE huntbots
                        SET unclaimed_coins = 0, battery = 0,
                            last_tick = :t, hunts_done = hunts_done + :h
                        WHERE guild_id = :g AND user_id = :u
                          AND unclaimed_coins = :seen_coins AND battery = :seen_battery
                        """
                    ),
                    {
                        "t": now, "h": state.battery, "g": player.guild_id, "u": player.user_id,
                        "seen_coins": state.unclaimed_coins, "seen_battery": state.battery,
                    },
                )
            ).rowcount
            if claim == 0:
                # a concurrent collect claimed this bank between our read and write
                raise HuntBotError("Nothing to collect — the battery is still charging.")

            coins = int(state.unclaimed_coins * (1 + harvest_bonus))
            items: list[Equipment] = []
            if coins:
                await conn.execute(
                    text("UPDATE players SET balance = balance + :c, updated_at = :t WHERE guild_id = :g AND user_id = :u"),
                    {"c": coins, "t": now, "g": player.guild_id, "u": player.user_id},
                )
                await conn.execute(
                    _SQL_ECO_LOG,
                    {"g": player.guild_id, "u": player.user_id, "d": coins, "r": "huntbot:collect", "b": player.balance + coins, "t": now},
                )
            item_rows = (
                await conn.execute(
                    text("SELECT item_key FROM huntbot_items WHERE guild_id = :g AND user_id = :u"),
                    {"g": player.guild_id, "u": player.user_id},
                )
            ).mappings().all()
            for row in item_rows:
                template = self.content.equipment_template(row["item_key"])
                if template is None:
                    continue
                item = template.roll(self._rng, self.content.lowest_tier)
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
                items.append(item)
            await conn.execute(
                text("DELETE FROM huntbot_items WHERE guild_id = :g AND user_id = :u"),
                {"g": player.guild_id, "u": player.user_id},
            )
        player.balance += coins
        self.log.info("user=%s collected %d coins, %d items from huntbot", player.user_id, coins, len(items))
        await self.bus.publish(
            GameEvent(category="huntbot", action="collect", guild_id=guild_id, user_id=player.user_id,
                      message=f"collected {coins:,} coins + {len(items)} items")
        )
        return coins, items

    # -- simulation --------------------------------------------------------------------

    def _simulate_tick_level(self, level: int) -> tuple[int, list[str]]:
        """Return (coins, item_keys) produced by one hunt cycle."""
        coins = self.income_per_tick(level)
        items: list[str] = []
        if self._rng.random() < self.settings.huntbot.item_drop_chance:
            templates = self.content.all_equipment()
            items.append(self._rng.choice(templates).key)
        return coins, items

    async def _tick_user(self, guild_id: int, user_id: int, harvest_bonus: float) -> None:
        """One hunt cycle, as a single read-modify-write transaction.

        State is re-read *inside* the transaction so a concurrent
        collect()/toggle() commits first and this tick simply operates on
        the fresh values (or skips). Coins and items are relative
        operations (increment / insert), so ticks cannot resurrect
        collected rewards.
        """
        async with self.db.transaction() as conn:
            row = (
                await conn.execute(
                    text("SELECT level, active, battery FROM huntbots WHERE guild_id = :g AND user_id = :u"),
                    {"g": guild_id, "u": user_id},
                )
            ).mappings().first()
            if row is None or not row["active"]:
                return
            if row["battery"] >= self.settings.huntbot.battery_capacity:
                return  # battery full; owner must collect

            gained_coins, gained_items = self._simulate_tick_level(int(row["level"]))
            gained_coins = int(gained_coins * (1 + harvest_bonus) * self.efficiency(int(row["level"])))

            await conn.execute(
                _SQL_TICK_UPDATE,
                {
                    "c": gained_coins, "cap": self.settings.huntbot.battery_capacity,
                    "t": _now_iso(), "g": guild_id, "u": user_id,
                },
            )
            # cap banked items at battery capacity, mirroring coin banking
            capacity = self.settings.huntbot.battery_capacity
            banked = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM huntbot_items WHERE guild_id = :g AND user_id = :u"),
                    {"g": guild_id, "u": user_id},
                )
            ).scalar_one()
            for key in gained_items[: max(0, capacity - int(banked))]:
                await conn.execute(_SQL_INSERT_ITEM, {"g": guild_id, "u": user_id, "k": key})
        self.log.debug("huntbot tick user=%s +%d coins %d items", user_id, gained_coins, len(gained_items))

    async def _reconcile_offline(self) -> None:
        """Credit progress for bots that ticked while the process was down."""
        rows = await self.db.fetch_all("SELECT * FROM huntbots WHERE active = 1")
        now = datetime.now(timezone.utc)
        for row in rows:
            guild_id, user_id = int(row["guild_id"]), int(row["user_id"])
            if not row["last_tick"]:
                await self.db.execute(
                    "UPDATE huntbots SET last_tick = :t WHERE guild_id = :g AND user_id = :u",
                    {"t": _now_iso(), "g": guild_id, "u": user_id},
                )
                continue
            last = datetime.fromisoformat(row["last_tick"])
            elapsed = (now - last).total_seconds()
            ticks = int(elapsed // self.settings.huntbot.tick_seconds)
            if ticks <= 0:
                continue
            capacity_left = max(0, self.settings.huntbot.battery_capacity - row["battery"])
            credited = min(ticks, capacity_left)
            level = int(row["level"])
            coins, items = 0, []
            for _ in range(credited):
                c, i = self._simulate_tick_level(level)
                coins += int(c * self.efficiency(level))
                items.extend(i)
            items = items[:capacity_left]
            new_battery = row["battery"] + credited
            advanced = last.timestamp() + credited * self.settings.huntbot.tick_seconds
            await self.db.execute(
                text(
                    """
                    UPDATE huntbots
                    SET unclaimed_coins = unclaimed_coins + :c,
                        battery = :b, last_tick = :t, hunts_done = hunts_done + :h
                    WHERE guild_id = :g AND user_id = :u
                    """
                ),
                {
                    "c": coins, "b": new_battery,
                    "t": datetime.fromtimestamp(advanced, tz=timezone.utc).isoformat(),
                    "h": credited, "g": guild_id, "u": user_id,
                },
            )
            for key in items:
                await self.db.execute(_SQL_INSERT_ITEM, {"g": guild_id, "u": user_id, "k": key})
            if credited:
                self.log.info(
                    "offline reconcile user=%s credited %d ticks (+%d coins)",
                    user_id, credited, coins,
                )

    async def _loop(self) -> None:
        """Background heartbeat: tick every loop_interval, simulating due cycles."""
        try:
            while True:
                await asyncio.sleep(self.settings.huntbot.loop_interval)
                await self._loop_iteration()
        except asyncio.CancelledError:
            self.log.debug("huntbot loop cancelled")
            raise

    async def _loop_iteration(self) -> None:
        """One heartbeat pass (extracted for testability)."""
        try:
            rows = await self.db.fetch_all(
                """
                SELECT hb.guild_id, hb.user_id,
                       COALESCE((SELECT level FROM upgrades
                                 WHERE guild_id = hb.guild_id AND user_id = hb.user_id
                                   AND upgrade_key = 'harvest'), 0) AS harvest
                FROM huntbots hb
                WHERE hb.active = 1 AND hb.battery < :cap
                """,
                {"cap": self.settings.huntbot.battery_capacity},
            )
            harvest_spec = self.content.upgrade("harvest")
            for row in rows:
                await self._tick_user(
                    int(row["guild_id"]), int(row["user_id"]),
                    row["harvest"] * (harvest_spec.effect_per_level if harvest_spec else 0.0),
                )
        except Exception:
            self.log.exception("huntbot loop iteration failed")
