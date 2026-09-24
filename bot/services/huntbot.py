"""Huntbot service: an automated hunting companion.

Model
-----
* Purchase once (level 1), then upgrade levels.
* While *active*, every ``CONFIG.huntbot_tick_seconds`` it completes one
  hunt cycle, banking coins (and occasionally equipment) into an
  *unclaimed* pool capped by battery capacity — so owners must collect.
* Offline progress: on boot, elapsed wall-clock is reconciled per bot,
  crediting up to a full battery of ticks that happened while away.

An ``asyncio`` background task drives the loop; cancellation is handled
gracefully on shutdown.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from bot.config import CONFIG
from bot.core.database import Database
from bot.core.exceptions import HuntBotError, InsufficientFundsError
from bot.models.items import Equipment, ItemRegistry
from bot.models.player import Player
from bot.models.rarities import Rarity
from bot.services.base import BaseService
from bot.services.upgrades import UpgradeCatalog

HUNTBOT_COST_GROWTH: float = 1.6  # per-level upgrade cost growth


@dataclass(slots=True)
class HuntBotState:
    user_id: int
    level: int
    active: bool
    battery: int
    last_tick: datetime | None
    unclaimed_coins: int
    unclaimed_items: list[str]
    hunts_done: int

    @property
    def is_owned(self) -> bool:
        return True

    @property
    def income_per_tick(self) -> int:
        base = 35 + self.level * 15
        return base

    @property
    def efficiency(self) -> float:
        return 1.0 + self.level * 0.1


class HuntBotService(BaseService):
    log_name = "gacha.huntbot"

    def __init__(self, db: Database, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._task: asyncio.Task | None = None
        super().__init__(db)

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

    # -- state helpers -------------------------------------------------------------

    async def get_state(self, user_id: int) -> HuntBotState | None:
        row = await self.db.fetch_one("SELECT * FROM huntbots WHERE user_id = ?", (str(user_id),))
        if row is None:
            return None
        return HuntBotState(
            user_id=user_id,
            level=row["level"],
            active=bool(row["active"]),
            battery=row["battery"],
            last_tick=datetime.fromisoformat(row["last_tick"]) if row["last_tick"] else None,
            unclaimed_coins=row["unclaimed_coins"],
            unclaimed_items=json.loads(row["unclaimed_items"] or "[]"),
            hunts_done=row["hunts_done"],
        )

    async def require_state(self, user_id: int) -> HuntBotState:
        state = await self.get_state(user_id)
        if state is None:
            raise HuntBotError("You don't own a huntbot yet — buy one with `!huntbot buy`.")
        return state

    # -- ownership / control ----------------------------------------------------------

    def price(self, level: int) -> int:
        """Price of the next level (level 0 == purchase)."""
        return int(CONFIG.huntbot_base_cost * HUNTBOT_COST_GROWTH ** level)

    async def buy(self, player: Player) -> int:
        state = await self.get_state(player.user_id)
        if state is not None:
            raise HuntBotError("You already own a huntbot — use `!huntbot upgrade`.")
        cost = self.price(0)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (cost, str(player.user_id)),
            )
            await conn.execute(
                "INSERT INTO huntbots (user_id, level, active, last_tick) VALUES (?, 1, 1, ?)",
                (str(player.user_id), self._now_iso()),
            )
            await conn.execute(
                "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                (str(player.user_id), -cost, "huntbot:buy", player.balance - cost),
            )
        player.balance -= cost
        self.log.info("user=%s bought huntbot (-%d)", player.user_id, cost)
        return cost

    async def upgrade(self, player: Player) -> tuple[int, int]:
        state = await self.require_state(player.user_id)
        cost = self.price(state.level)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (cost, str(player.user_id)),
            )
            await conn.execute(
                "UPDATE huntbots SET level = level + 1 WHERE user_id = ?",
                (str(player.user_id),),
            )
            await conn.execute(
                "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                (str(player.user_id), -cost, "huntbot:upgrade", player.balance - cost),
            )
        player.balance -= cost
        self.log.info("user=%s upgraded huntbot -> level %d (-%d)", player.user_id, state.level + 1, cost)
        return state.level + 1, cost

    async def set_active(self, player: Player, active: bool) -> None:
        state = await self.require_state(player.user_id)
        if state.active == active:
            raise HuntBotError(f"Huntbot is already {'active' if active else 'idle'}.")
        await self.db.execute(
            "UPDATE huntbots SET active = ?, last_tick = ? WHERE user_id = ?",
            (1 if active else 0, self._now_iso(), str(player.user_id)),
        )
        self.log.info("user=%s huntbot active=%s", player.user_id, active)

    async def collect(self, player: Player, harvest_bonus: float = 0.0) -> tuple[int, list[Equipment]]:
        """Sweep unclaimed rewards into the player's balance/inventory."""
        state = await self.require_state(player.user_id)
        coins = int(state.unclaimed_coins * (1 + harvest_bonus))
        items: list[Equipment] = []
        if not coins and not state.unclaimed_items:
            raise HuntBotError("Nothing to collect — the battery is still charging.")

        async with self.db.transaction() as conn:
            if coins:
                await conn.execute(
                    "UPDATE players SET balance = balance + ?, updated_at = datetime('now') WHERE user_id = ?",
                    (coins, str(player.user_id)),
                )
                await conn.execute(
                    "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                    (str(player.user_id), coins, "huntbot:collect", player.balance + coins),
                )
            for key in state.unclaimed_items:
                template = ItemRegistry.equipment_template(key)
                if template is None:
                    continue
                item = template.roll(self._rng, Rarity.COMMON)
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
                items.append(item)
            await conn.execute(
                """
                UPDATE huntbots
                SET unclaimed_coins = 0, unclaimed_items = '', battery = 0,
                    last_tick = ?, hunts_done = hunts_done + ?
                WHERE user_id = ?
                """,
                (self._now_iso(), state.battery, str(player.user_id)),
            )
        player.balance += coins
        self.log.info("user=%s collected %d coins, %d items from huntbot", player.user_id, coins, len(items))
        return coins, items

    # -- simulation --------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _simulate_tick(self, state: HuntBotState) -> tuple[int, list[str]]:
        """Return (coins, item_keys) produced by one hunt cycle."""
        coins = state.income_per_tick
        items: list[str] = []
        if self._rng.random() < 0.08:
            templates = ItemRegistry.all_equipment()
            items.append(self._rng.choice(templates).key)
        return coins, items

    async def _tick_user(self, user_id: int, harvest_bonus: float) -> None:
        state = await self.get_state(user_id)
        if state is None or not state.active:
            return
        if state.battery >= CONFIG.huntbot_battery_capacity:
            return  # battery full; owner must collect

        gained_coins, gained_items = self._simulate_tick(state)
        gained_coins = int(gained_coins * (1 + harvest_bonus) * state.efficiency)
        new_items = (state.unclaimed_items + gained_items)[: CONFIG.huntbot_battery_capacity]

        await self.db.execute(
            """
            UPDATE huntbots
            SET unclaimed_coins = unclaimed_coins + ?, unclaimed_items = ?,
                battery = MIN(battery + 1, ?), last_tick = ?, hunts_done = hunts_done + 1
            WHERE user_id = ?
            """,
            (
                gained_coins, json.dumps(new_items), CONFIG.huntbot_battery_capacity,
                self._now_iso(), str(user_id),
            ),
        )
        self.log.debug("huntbot tick user=%s +%d coins %d items", user_id, gained_coins, len(gained_items))

    async def _reconcile_offline(self) -> None:
        """Credit progress for bots that ticked while the process was down."""
        rows = await self.db.fetch_all("SELECT * FROM huntbots WHERE active = 1")
        now = datetime.now(timezone.utc)
        for row in rows:
            if not row["last_tick"]:
                await self.db.execute(
                    "UPDATE huntbots SET last_tick = ? WHERE user_id = ?",
                    (self._now_iso(), row["user_id"]),
                )
                continue
            last = datetime.fromisoformat(row["last_tick"])
            elapsed = (now - last).total_seconds()
            ticks = int(elapsed // CONFIG.huntbot_tick_seconds)
            if ticks <= 0:
                continue
            capacity_left = max(0, CONFIG.huntbot_battery_capacity - row["battery"])
            credited = min(ticks, capacity_left)
            state = HuntBotState(
                user_id=int(row["user_id"]), level=row["level"], active=True,
                battery=row["battery"], last_tick=last,
                unclaimed_coins=row["unclaimed_coins"], unclaimed_items=[],
                hunts_done=row["hunts_done"],
            )
            coins, items = 0, []
            for _ in range(credited):
                c, i = self._simulate_tick(state)
                coins += int(c * state.efficiency)
                items.extend(i)
            items = items[:capacity_left]
            new_battery = row["battery"] + credited
            advanced = last.timestamp() + credited * CONFIG.huntbot_tick_seconds
            await self.db.execute(
                """
                UPDATE huntbots
                SET unclaimed_coins = unclaimed_coins + ?, unclaimed_items = ?,
                    battery = ?, last_tick = ?, hunts_done = hunts_done + ?
                WHERE user_id = ?
                """,
                (
                    coins, json.dumps(list(dict.fromkeys(items))), new_battery,
                    datetime.fromtimestamp(advanced, tz=timezone.utc).isoformat(),
                    credited, row["user_id"],
                ),
            )
            if credited:
                self.log.info(
                    "offline reconcile user=%s credited %d ticks (+%d coins)",
                    row["user_id"], credited, coins,
                )

    async def _loop(self) -> None:
        """Background heartbeat: tick every 60s, simulating due cycles."""
        try:
            while True:
                await asyncio.sleep(60)
                try:
                    rows = await self.db.fetch_all(
                        """
                        SELECT hb.user_id,
                               COALESCE((SELECT level FROM upgrades
                                         WHERE user_id = hb.user_id AND upgrade_key = 'harvest'), 0) AS harvest
                        FROM huntbots hb
                        WHERE hb.active = 1 AND hb.battery < ?
                        """,
                        (CONFIG.huntbot_battery_capacity,),
                    )
                    for row in rows:
                        await self._tick_user(
                            int(row["user_id"]),
                            row["harvest"] * UpgradeCatalog.get("harvest").effect_per_level,
                        )
                except Exception:
                    self.log.exception("huntbot loop iteration failed")
        except asyncio.CancelledError:
            self.log.debug("huntbot loop cancelled")
            raise
