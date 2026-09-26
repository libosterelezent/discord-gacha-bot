"""Economy service: balances, transfers, daily/work rewards, gambling.

All mutations happen inside a DB transaction and are appended to the
`economy_log` audit table, and published to the event bus for the
maintainer logging sink. Every operation is **scope-aware**: in
guild-scoped mode (default) balances are per-server; global mode maps
everything onto the sentinel guild ``0`` via :func:`scope_id`.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from sqlalchemy import text

from bot.core.decorators import timed
from bot.core.events import GameEvent
from bot.core.exceptions import (
    GachaBotError,
    InsufficientFundsError,
    NegativeAmountError,
    PlayerNotFoundError,
)
from bot.models.player import Player
from bot.services.base import BaseService


if TYPE_CHECKING:
    from bot.config import GameSettings

GLOBAL_GUILD_ID: int = 0  # sentinel scope for global economy mode

Scope = Literal["guild", "global"]


def scope_id(guild_id: int | None, settings: "GameSettings") -> int:
    """Resolve the storage scope: guild rows, or the global sentinel."""
    if settings.economy.scope == "global" or guild_id is None:
        return GLOBAL_GUILD_ID
    return int(guild_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- shared SQL fragments (named binds, portable across sqlite/postgres) --------

_SQL_UPSERT_PLAYER = text(
    """
    INSERT INTO players (guild_id, user_id, balance, created_at, updated_at)
    VALUES (:g, :u, :b, :t, :t)
    ON CONFLICT DO NOTHING
    """
)
_SQL_UPSERT_GLOBAL_USER = text(
    """
    INSERT INTO global_users (user_id, first_seen, last_seen)
    VALUES (:u, :t, :t)
    ON CONFLICT (user_id) DO UPDATE SET last_seen = :t
    """
)
_SQL_ADD_BALANCE = text(
    """
    UPDATE players SET balance = balance + :d, updated_at = :t
    WHERE guild_id = :g AND user_id = :u
    """
)
_SQL_SUBTRACT_BALANCE_GUARDED = text(
    """
    UPDATE players SET balance = balance - :d, updated_at = :t
    WHERE guild_id = :g AND user_id = :u AND balance >= :d
    """
)
_SQL_SELECT_BALANCE = text(
    "SELECT balance FROM players WHERE guild_id = :g AND user_id = :u"
)
_SQL_INSERT_ECO_LOG = text(
    """
    INSERT INTO economy_log (guild_id, user_id, delta, reason, balance_after, created_at)
    VALUES (:g, :u, :d, :r, :b, :t)
    """
)


class EconomyService(BaseService):
    log_name = "gacha.economy"

    async def on_start(self) -> None:
        self.log.info("Economy service ready (scope=%s)", self.settings.economy.scope)

    # -- events ------------------------------------------------------------------

    async def _publish(
        self, category: str, action: str, guild_id: int | None, user_id: int, message: str, **fields: object
    ) -> None:
        await self.bus.publish(
            GameEvent(
                category=category, action=action, guild_id=guild_id, user_id=user_id,
                message=message, fields=fields,
            )
        )

    # -- player lifecycle ----------------------------------------------------------

    async def ensure_player(self, guild_id: int | None, user_id: int) -> Player:
        """Get-or-create the player aggregate (UPSERT semantics)."""
        scope = scope_id(guild_id, self.settings)
        row = await self.db.fetch_one(
            "SELECT * FROM players WHERE guild_id = :g AND user_id = :u", {"g": scope, "u": user_id}
        )
        if row is None:
            now = _now_iso()
            async with self.db.transaction() as conn:
                await conn.execute(
                    _SQL_UPSERT_PLAYER,
                    {"g": scope, "u": user_id, "b": self.settings.economy.starting_balance, "t": now},
                )
                await conn.execute(_SQL_UPSERT_GLOBAL_USER, {"u": user_id, "t": now})
            row = await self.db.fetch_one(
                "SELECT * FROM players WHERE guild_id = :g AND user_id = :u", {"g": scope, "u": user_id}
            )
            self.log.info("Created new player guild=%s user=%s", scope, user_id)
        if row is None:  # pragma: no cover - should be impossible
            raise PlayerNotFoundError()

        player = Player(
            user_id=user_id,
            guild_id=scope,
            balance=row["balance"],
            shards=row["shards"],
            xp=row["xp"],
            level=row["level"],
            total_pulls=row["total_pulls"],
            pity_counter=row["pity_counter"],
        )
        rows = await self.db.fetch_all(
            "SELECT upgrade_key, level FROM upgrades WHERE guild_id = :g AND user_id = :u",
            {"g": scope, "u": user_id},
        )
        player.upgrades = {r["upgrade_key"]: r["level"] for r in rows}
        return player

    # -- balance ops ----------------------------------------------------------------

    async def _apply_delta(self, guild_id: int | None, user_id: int, delta: int, reason: str) -> int:
        """Atomically adjust balance; returns the new balance."""
        scope = scope_id(guild_id, self.settings)
        now = _now_iso()
        async with self.db.transaction() as conn:
            result = await conn.execute(
                _SQL_ADD_BALANCE, {"g": scope, "u": user_id, "d": delta, "t": now}
            )
            if result.rowcount == 0:
                raise PlayerNotFoundError()
            new_balance = (await conn.execute(_SQL_SELECT_BALANCE, {"g": scope, "u": user_id})).scalar_one()
            if new_balance < 0:
                raise InsufficientFundsError(-delta, int(new_balance) - delta)
            await conn.execute(
                _SQL_INSERT_ECO_LOG,
                {"g": scope, "u": user_id, "d": delta, "r": reason, "b": int(new_balance), "t": now},
            )
        return int(new_balance)

    async def deposit(self, guild_id: int | None, user_id: int, amount: int, reason: str = "deposit") -> int:
        if amount <= 0:
            raise NegativeAmountError()
        new_balance = await self._apply_delta(guild_id, user_id, amount, reason)
        await self._publish("economy", "deposit", guild_id, user_id, f"+{amount:,} ({reason}) \u2192 {new_balance:,}")
        return new_balance

    async def withdraw(self, guild_id: int | None, user_id: int, amount: int, reason: str = "withdraw") -> int:
        if amount <= 0:
            raise NegativeAmountError()
        new_balance = await self._apply_delta(guild_id, user_id, -amount, reason)
        await self._publish("economy", "withdraw", guild_id, user_id, f"-{amount:,} ({reason}) \u2192 {new_balance:,}")
        return new_balance

    async def balance(self, guild_id: int | None, user_id: int) -> int:
        scope = scope_id(guild_id, self.settings)
        val = await self.db.fetch_val(
            "SELECT balance FROM players WHERE guild_id = :g AND user_id = :u", {"g": scope, "u": user_id}
        )
        return int(val or 0)

    async def transfer(self, guild_id: int | None, sender: Player, recipient_id: int, amount: int) -> int:
        if amount <= 0:
            raise NegativeAmountError()
        if sender.user_id == recipient_id:
            raise GachaBotError("You cannot pay yourself.")
        if sender.balance < amount:
            raise InsufficientFundsError(amount, sender.balance)
        scope = scope_id(guild_id, self.settings)
        await self.ensure_player(guild_id, recipient_id)
        now = _now_iso()
        async with self.db.transaction() as conn:
            # guarded subtraction: the balance check races no concurrent spend
            result = await conn.execute(
                _SQL_SUBTRACT_BALANCE_GUARDED,
                {"g": scope, "u": sender.user_id, "d": amount, "t": now},
            )
            if result.rowcount == 0:
                raise InsufficientFundsError(amount, sender.balance)
            await conn.execute(
                _SQL_ADD_BALANCE, {"g": scope, "u": recipient_id, "d": amount, "t": now}
            )
            sender_balance = (
                await conn.execute(_SQL_SELECT_BALANCE, {"g": scope, "u": sender.user_id})
            ).scalar_one()
            recipient_balance = (
                await conn.execute(_SQL_SELECT_BALANCE, {"g": scope, "u": recipient_id})
            ).scalar_one()
            await conn.execute(
                _SQL_INSERT_ECO_LOG,
                {"g": scope, "u": sender.user_id, "d": -amount, "r": f"transfer->{recipient_id}", "b": int(sender_balance), "t": now},
            )
            await conn.execute(
                _SQL_INSERT_ECO_LOG,
                {"g": scope, "u": recipient_id, "d": amount, "r": f"transfer<-{sender.user_id}", "b": int(recipient_balance), "t": now},
            )
        sender.balance = int(sender_balance)
        await self._publish(
            "economy", "transfer", guild_id, sender.user_id,
            f"sent {amount:,} to <@{recipient_id}>", recipient=recipient_id, amount=amount,
        )
        return sender.balance

    # -- rewards -----------------------------------------------------------------------

    async def daily(self, guild_id: int | None, player: Player) -> int:
        self.cooldowns.check(player.guild_id, player.user_id, "daily")
        reward = self.settings.economy.daily_reward + player.level * self.settings.economy.daily_level_bonus
        await self._apply_delta(guild_id, player.user_id, reward, "daily")
        await self.cooldowns.trigger(player.guild_id, player.user_id, "daily")
        await self._publish("economy", "daily", guild_id, player.user_id, f"claimed daily +{reward:,}")
        return reward

    @timed()
    async def work(self, guild_id: int | None, player: Player) -> int:
        self.cooldowns.check(player.guild_id, player.user_id, "work")
        amount = random.randint(self.settings.economy.work_min, self.settings.economy.work_max)
        greed = self.content.upgrade("greed")
        profile_bonus = player.upgrades.get("greed", 0) * (greed.effect_per_level if greed else 0.0)
        amount = int(amount * (1 + profile_bonus))
        await self._apply_delta(guild_id, player.user_id, amount, "work")
        await self.cooldowns.trigger(player.guild_id, player.user_id, "work")
        await self._publish("economy", "work", guild_id, player.user_id, f"worked for +{amount:,}")
        return amount

    # -- gamble ---------------------------------------------------------------------------

    async def gamble(self, guild_id: int | None, player: Player, amount: int) -> int:
        """50/50 double-or-nothing. Returns delta (positive = won)."""
        if amount <= 0:
            raise NegativeAmountError()
        if player.balance < amount:
            raise InsufficientFundsError(amount, player.balance)
        won = random.random() < 0.5
        delta = amount if won else -amount
        await self._apply_delta(guild_id, player.user_id, delta, "gamble")
        await self._publish(
            "economy", "gamble", guild_id, player.user_id,
            f"{'won' if won else 'lost'} {amount:,}", won=won, amount=amount,
        )
        return delta

    # -- leaderboards ------------------------------------------------------------------------

    async def leaderboard(self, guild_id: int | None, limit: int = 10, scope: Scope = "guild") -> list[tuple[int, int, int]]:
        """Rows of (user_id, balance, level) ordered by balance.

        ``scope='global'`` aggregates per-user totals across all guilds.
        """
        if scope == "global":
            rows = await self.db.fetch_all(
                """
                SELECT user_id, SUM(balance) AS balance, MAX(level) AS level
                FROM players GROUP BY user_id
                ORDER BY balance DESC LIMIT :lim
                """,
                {"lim": limit},
            )
            return [(int(r["user_id"]), int(r["balance"]), int(r["level"])) for r in rows]
        effective = scope_id(guild_id, self.settings)
        rows = await self.db.fetch_all(
            """
            SELECT user_id, balance, level FROM players
            WHERE guild_id = :g ORDER BY balance DESC LIMIT :lim
            """,
            {"g": effective, "lim": limit},
        )
        return [(int(r["user_id"]), int(r["balance"]), int(r["level"])) for r in rows]

    async def add_xp_and_level(self, player: Player, xp_gain: int) -> int | None:
        """Persist XP/level changes. Returns new level if levelled up."""
        levelled = player.add_xp(xp_gain)
        await self.db.execute(
            """
            UPDATE players SET xp = :xp, level = :lvl, updated_at = :t
            WHERE guild_id = :g AND user_id = :u
            """,
            {
                "xp": player.xp, "lvl": player.level, "t": _now_iso(),
                "g": player.guild_id, "u": player.user_id,
            },
        )
        return player.level if levelled else None
