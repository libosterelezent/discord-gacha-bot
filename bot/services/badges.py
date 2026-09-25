"""Badge service: guild-scoped and global player awards.

Badges are declared in ``bot/content/data/badges.json`` with a scope:

* ``guild``  — awarded per server (e.g. event-winner badges) and stored
  against that guild only;
* ``global`` — follows the player across every server (e.g. veteran or
  maintainer-recognised titles).

This is the foundation for future global achievements/ranks without
any schema changes: rows with a NULL ``guild_id`` are global awards.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import text

from bot.core.events import GameEvent
from bot.core.exceptions import GachaBotError
from bot.services.base import BaseService

if TYPE_CHECKING:
    from bot.content.registry import BadgeSpec


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SQL_GRANT = text(
    """
    INSERT INTO badges (user_id, badge_key, guild_id, granted_at, granted_by)
    VALUES (:u, :k, :g, :t, :by)
    ON CONFLICT (user_id, badge_key, guild_id) DO NOTHING
    """
)


class BadgeService(BaseService):
    log_name = "gacha.badges"

    async def on_start(self) -> None:
        self.log.info("Badge service ready (%d badges)", len(self.content.all_badges()))

    async def grant(
        self, guild_id: int | None, user_id: int, badge_key: str, granted_by: int | None = None
    ) -> "BadgeSpec":
        """Award a badge. Guild badges bind to `guild_id`; global ones to NULL."""
        spec = self.content.badge(badge_key)
        if spec is None:
            raise GachaBotError(f"Unknown badge `{badge_key}`.")
        storage_guild = guild_id if spec.scope == "guild" else None
        await self.db.execute(
            _SQL_GRANT,
            {"u": user_id, "k": badge_key, "g": storage_guild, "t": _now_iso(), "by": granted_by},
        )
        await self.bus.publish(
            GameEvent(
                category="admin", action="badge_grant", guild_id=guild_id, user_id=user_id,
                message=f"badge {spec.emoji} {spec.name} ({spec.scope}) awarded",
            )
        )
        return spec

    async def revoke(self, guild_id: int | None, user_id: int, badge_key: str) -> "BadgeSpec":
        spec = self.content.badge(badge_key)
        if spec is None:
            raise GachaBotError(f"Unknown badge `{badge_key}`.")
        storage_guild = guild_id if spec.scope == "guild" else None
        await self.db.execute(
            """
            DELETE FROM badges
            WHERE user_id = :u AND badge_key = :k
              AND ((:g IS NULL AND guild_id IS NULL) OR guild_id = :g)
            """,
            {"u": user_id, "k": badge_key, "g": storage_guild},
        )
        await self.bus.publish(
            GameEvent(
                category="admin", action="badge_revoke", guild_id=guild_id, user_id=user_id,
                message=f"badge {spec.emoji} {spec.name} revoked",
            )
        )
        return spec

    async def player_badges(self, user_id: int, guild_id: int | None = None) -> list[tuple["BadgeSpec", int | None]]:
        """All badges a player holds: global + (optionally) guild-specific.

        Returns (spec, holder_guild_id) pairs.
        """
        rows = await self.db.fetch_all(
            """
            SELECT badge_key, guild_id FROM badges
            WHERE user_id = :u AND (guild_id IS NULL OR guild_id = :g)
            """,
            {"u": user_id, "g": guild_id},
        )
        result: list[tuple[BadgeSpec, int | None]] = []
        for row in rows:
            spec = self.content.badge(row["badge_key"])
            if spec is not None:
                result.append((spec, row["guild_id"]))
        return result

    async def badge_strings(self, user_id: int, guild_id: int | None = None) -> list[str]:
        """Compact display strings for profile cards."""
        return [f"{spec.emoji} **{spec.name}**" for spec, _guild in await self.player_badges(user_id, guild_id)]
