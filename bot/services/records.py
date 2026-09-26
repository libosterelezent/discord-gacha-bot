"""Hall of Records: guild-scoped historical bests and firsts.

Unlike leaderboards ("who is strongest right now?"), records are
history: the first Mythic pull, the largest single hunt, the biggest
gamble win. They are only ever *set or beaten* — never reset — and each
new record publishes an event so the logging sink can announce it.

Captures happen in the cogs (thin, no service signature churn); this
service only owns storage and best-so-far semantics.
"""
from __future__ import annotations

from bot.core.events import GameEvent
from bot.core.util import now_iso
from bot.services.base import BaseService


class RecordService(BaseService):
    log_name = "gacha.records"

    async def on_start(self) -> None:
        self.log.info("Record service ready")

    async def submit_max(self, guild_id: int | None, key: str, label: str,
                         user_id: int, value: int) -> bool:
        """Set the record if `value` beats the current best. Returns True when set."""
        if guild_id is None or value <= 0:
            return False
        row = await self.db.fetch_one(
            "SELECT user_id, value FROM records WHERE guild_id = :g AND key = :k",
            {"g": guild_id, "k": key},
        )
        if row is not None and int(row["value"]) >= value:
            return False
        await self.db.execute(
            """
            INSERT INTO records (guild_id, key, label, user_id, value, set_at)
            VALUES (:g, :k, :l, :u, :v, :t)
            ON CONFLICT (guild_id, key) DO UPDATE SET
                user_id = :u, value = :v, set_at = :t, label = :l
            """,
            {"g": guild_id, "k": key, "l": label, "u": user_id, "v": value, "t": now_iso()},
        )
        await self._announce(guild_id, key, label, user_id, value)
        return True

    async def claim_first(self, guild_id: int | None, key: str, label: str,
                          user_id: int, value: int = 1) -> bool:
        """Claim a one-time 'first' record. Returns True if this call claimed it."""
        if guild_id is None:
            return False
        changed = await self.db.execute(
            """
            INSERT INTO records (guild_id, key, label, user_id, value, set_at)
            VALUES (:g, :k, :l, :u, :v, :t)
            ON CONFLICT (guild_id, key) DO NOTHING
            """,
            {"g": guild_id, "k": key, "l": label, "u": user_id, "v": value, "t": now_iso()},
        )
        if changed == 0:
            return False
        await self._announce(guild_id, key, label, user_id, value)
        return True

    async def all_records(self, guild_id: int) -> list[dict]:
        rows = await self.db.fetch_all(
            "SELECT key, label, user_id, value, set_at FROM records "
            "WHERE guild_id = :g ORDER BY key",
            {"g": guild_id},
        )
        return [dict(r) for r in rows]

    async def _announce(self, guild_id: int, key: str, label: str, user_id: int, value: int) -> None:
        await self.bus.publish(
            GameEvent(
                category="records", action="new_record", guild_id=guild_id, user_id=user_id,
                message=f"new record: {label} — {value:,}",
                fields={"key": key, "value": value},
            )
        )
