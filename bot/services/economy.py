"""Economy service: balances, transfers, daily/work rewards, gambling.

All mutations happen inside a DB transaction and are appended to the
`economy_log` audit table.
"""
from __future__ import annotations

import random
from typing import Iterable

from bot.config import CONFIG, CONSTANTS
from bot.core.database import Database
from bot.core.decorators import rate_limited, timed
from bot.core.exceptions import (
    CooldownError,
    InsufficientFundsError,
    NegativeAmountError,
    PlayerNotFoundError,
)
from bot.models.player import Player
from bot.services.base import BaseService
from bot.services.upgrades import UpgradeCatalog

_IN_MEMORY_COOLDOWNS: dict[tuple[int, str], float] = {}


def _cooldown(user_id: int, action: str) -> float:
    """Seconds remaining for (user, action); 0.0 when ready."""
    import time

    until = _IN_MEMORY_COOLDOWNS.get((user_id, action), 0.0)
    return max(0.0, until - time.monotonic())


def _set_cooldown(user_id: int, action: str, seconds: float) -> None:
    import time

    _IN_MEMORY_COOLDOWNS[(user_id, action)] = time.monotonic() + seconds


class EconomyService(BaseService):
    log_name = "gacha.economy"

    async def on_start(self) -> None:
        self.log.info("Economy service ready")

    # -- player lifecycle ----------------------------------------------------

    @timed()
    async def ensure_player(self, user_id: int) -> Player:
        """Get-or-create the player aggregate (UPSERT semantics)."""
        row = await self.db.fetch_one("SELECT * FROM players WHERE user_id = ?", (str(user_id),))
        if row is None:
            await self.db.execute(
                "INSERT OR IGNORE INTO players (user_id, balance) VALUES (?, ?)",
                (str(user_id), CONFIG.starting_balance),
            )
            row = await self.db.fetch_one("SELECT * FROM players WHERE user_id = ?", (str(user_id),))
            self.log.info("Created new player user=%s", user_id)
        if row is None:  # pragma: no cover - should be impossible
            raise PlayerNotFoundError()
        player = Player(
            user_id=user_id,
            balance=row["balance"],
            shards=row["shards"],
            xp=row["xp"],
            level=row["level"],
            total_pulls=row["total_pulls"],
            pity_counter=row["pity_counter"],
        )
        # hydrate upgrades
        rows = await self.db.fetch_all(
            "SELECT upgrade_key, level FROM upgrades WHERE user_id = ?", (str(user_id),)
        )
        player.upgrades = {r["upgrade_key"]: r["level"] for r in rows}
        return player

    # -- balance ops -----------------------------------------------------------

    async def _apply_delta(self, user_id: int, delta: int, reason: str) -> int:
        """Atomically adjust balance; returns the new balance."""
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE players SET balance = balance + ?, updated_at = datetime('now') WHERE user_id = ?",
                (delta, str(user_id)),
            )
            if cur.rowcount == 0:
                raise PlayerNotFoundError()
            row = await conn.execute("SELECT balance FROM players WHERE user_id = ?", (str(user_id),))
            new_balance = (await row.fetchone())[0]
            if new_balance < 0:
                raise InsufficientFundsError(-delta, new_balance - delta)
            await conn.execute(
                "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                (str(user_id), delta, reason, new_balance),
            )
        return new_balance

    async def deposit(self, user_id: int, amount: int, reason: str = "deposit") -> int:
        if amount <= 0:
            raise NegativeAmountError()
        return await self._apply_delta(user_id, amount, reason)

    async def withdraw(self, user_id: int, amount: int, reason: str = "withdraw") -> int:
        if amount <= 0:
            raise NegativeAmountError()
        return await self._apply_delta(user_id, -amount, reason)

    async def balance(self, user_id: int) -> int:
        val = await self.db.fetch_val("SELECT balance FROM players WHERE user_id = ?", (str(user_id),))
        return int(val or 0)

    async def transfer(self, sender: Player, recipient_id: int, amount: int) -> int:
        if amount <= 0:
            raise NegativeAmountError()
        if sender.user_id == recipient_id:
            from bot.core.exceptions import GachaBotError
            raise GachaBotError("You cannot pay yourself.")
        if sender.balance < amount:
            raise InsufficientFundsError(amount, sender.balance)
        await self.ensure_player(recipient_id)
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?", (amount, str(sender.user_id))
            )
            await conn.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?", (amount, str(recipient_id))
            )
            await conn.execute(
                "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                (str(sender.user_id), -amount, f"transfer->{recipient_id}", sender.balance - amount),
            )
        sender.balance -= amount
        self.log.info("transfer %s -> %s (%d coins)", sender.user_id, recipient_id, amount)
        return sender.balance

    # -- rewards ----------------------------------------------------------------

    async def daily(self, player: Player) -> int:
        remaining = _cooldown(player.user_id, "daily")
        if remaining > 0:
            raise CooldownError(remaining / 3600, unit="hours")
        reward = CONFIG.daily_reward + player.level * 25
        await self._apply_delta(player.user_id, reward, "daily")
        _set_cooldown(player.user_id, "daily", CONFIG.daily_cooldown_hours * 3600)
        self.log.info("user=%s daily +%d", player.user_id, reward)
        return reward

    @rate_limited(window_seconds=3600, max_calls=1, key_arg="user_id")
    async def work(self, player: Player) -> int:
        remaining = _cooldown(player.user_id, "work")
        if remaining > 0:
            raise CooldownError(remaining, unit="seconds")
        amount = random.randint(CONFIG.work_min, CONFIG.work_max)
        profile_bonus = player.upgrades.get("greed", 0) * UpgradeCatalog.get("greed").effect_per_level
        amount = int(amount * (1 + profile_bonus))
        await self._apply_delta(player.user_id, amount, "work")
        _set_cooldown(player.user_id, "work", CONFIG.work_cooldown_seconds)
        return amount

    # -- gamble -------------------------------------------------------------------

    async def gamble(self, player: Player, amount: int) -> int:
        """50/50 double-or-nothing. Returns delta (positive = won)."""
        if amount <= 0:
            raise NegativeAmountError()
        if player.balance < amount:
            raise InsufficientFundsError(amount, player.balance)
        won = random.random() < 0.5
        delta = amount if won else -amount
        await self._apply_delta(player.user_id, delta, "gamble")
        self.log.info("user=%s gamble %s (%+d)", player.user_id, "won" if won else "lost", delta)
        return delta

    # -- leaderboard ----------------------------------------------------------------

    async def leaderboard(self, limit: int = 10) -> list[tuple[int, int, int]]:
        """Returns rows of (user_id, balance, level) ordered by balance."""
        rows = await self.db.fetch_all(
            "SELECT user_id, balance, level FROM players ORDER BY balance DESC LIMIT ?", (limit,)
        )
        return [(int(r["user_id"]), r["balance"], r["level"]) for r in rows]

    async def add_xp_and_level(self, player: Player, xp_gain: int) -> int | None:
        """Persist XP/level changes. Returns new level if levelled up."""
        levelled = player.add_xp(xp_gain)
        await self.db.execute(
            "UPDATE players SET xp = ?, level = ?, updated_at = datetime('now') WHERE user_id = ?",
            (player.xp, player.level, str(player.user_id)),
        )
        return player.level if levelled else None
