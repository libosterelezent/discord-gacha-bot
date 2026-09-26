"""Guild progression: reputation, relics and weekly expeditions.

The guild layer turns ordinary gameplay into server identity:

* **Reputation** — the guild earns XP collectively (hunts, pulls,
  expedition milestones); levels are cosmetic prestige.
* **Relics** — one server-wide passive (small xp/coin/luck bonus),
  swappable at most once per ISO week.
* **Expeditions** — a weekly objective (deterministic per week, so
  every server runs the same one) fed by normal play; milestones give
  reputation, completion pays every contributor.
* The cross-guild leaderboard ranks expedition progress for the
  current week across every server the bot is in — asynchronous
  server-vs-server without direct combat.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import text

from bot.core.events import GameEvent
from bot.core.exceptions import GachaBotError
from bot.services.base import BaseService

if TYPE_CHECKING:
    from bot.content.registry import ExpeditionSpec, RelicSpec

_WEEK_FORMAT = "%G-W%V"  # ISO week, matches expeditions.json

_SQL_UPSERT_GUILD = text(
    "INSERT INTO guild_state (guild_id) VALUES (:g) ON CONFLICT (guild_id) DO NOTHING"
)
_SQL_UPSERT_CONTRIB = text(
    """
    INSERT INTO guild_contributions (guild_id, user_id, week, hunts, pulls, coins)
    VALUES (:g, :u, :w, :h, :p, :c)
    ON CONFLICT (guild_id, user_id, week) DO UPDATE SET
        hunts = hunts + :h, pulls = pulls + :p, coins = coins + :c
    """
)
_SQL_BUMP_EXPEDITION = text(
    """
    UPDATE guild_state
    SET expedition_progress = expedition_progress + :d
    WHERE guild_id = :g AND expedition_week = :w
    """
)


@dataclass(slots=True)
class ExpeditionStatus:
    spec: "ExpeditionSpec | None"
    progress: int
    target: int
    milestones_hit: tuple[float, ...]
    done: bool

    @property
    def pct(self) -> float:
        if self.target <= 0:
            return 0.0
        return min(1.0, self.progress / self.target)


class GuildProgressService(BaseService):
    log_name = "gacha.guild"

    def __init__(self, db, content, settings, bus, cooldowns, economy) -> None:
        super().__init__(db, content, settings, bus, cooldowns)
        self.economy = economy

    async def on_start(self) -> None:
        self.log.info("Guild progression service ready")

    # -- helpers -----------------------------------------------------------------

    @staticmethod
    def current_week() -> str:
        return datetime.now(timezone.utc).strftime(_WEEK_FORMAT)

    @staticmethod
    def reputation_level(reputation: int) -> int:
        """Level curve: level n needs 100 * n^2 reputation."""
        return int(math.isqrt(max(reputation, 0) // 100))

    @staticmethod
    def level_title(level: int) -> str:
        titles = (
            (15, "Legendary Hunting Grounds"), (10, "Renowned Outpost"),
            (7, "Fortified Camp"), (4, "Established Lodge"), (2, "Campfire Guild"),
        )
        for floor_, title in titles:
            if level >= floor_:
                return title
        return "Wandering Band"

    async def _ensure(self, guild_id: int) -> dict:
        await self.db.execute(_SQL_UPSERT_GUILD, {"g": guild_id})
        row = await self.db.fetch_one(
            "SELECT * FROM guild_state WHERE guild_id = :g", {"g": guild_id}
        )
        assert row is not None
        return dict(row)

    # -- reputation ----------------------------------------------------------------

    async def add_reputation(self, guild_id: int, delta: int) -> int:
        if guild_id <= 0 or delta == 0:
            return 0
        before = (await self._ensure(guild_id))["reputation"]
        async with self.db.transaction() as conn:
            new_total = (
                await conn.execute(
                    text(
                        "UPDATE guild_state SET reputation = reputation + :d "
                        "WHERE guild_id = :g RETURNING reputation"
                    ),
                    {"d": delta, "g": guild_id},
                )
            ).scalar()
        level_before = self.reputation_level(int(before))
        level_after = self.reputation_level(int(new_total or 0))
        if level_after > level_before:
            await self._announce_level(guild_id, level_after)
        return int(new_total or 0)

    async def state(self, guild_id: int) -> dict:
        return await self._ensure(guild_id)

    # -- relics ----------------------------------------------------------------------

    async def get_relic(self, guild_id: int) -> "RelicSpec | None":
        row = await self._ensure(guild_id)
        return self.content.relic(row["relic_key"]) if row["relic_key"] else None

    async def set_relic(self, guild_id: int, key: str) -> "RelicSpec":
        spec = self.content.relic(key)
        if spec is None:
            raise GachaBotError(
                f"Unknown relic `{key}`. Available: "
                + ", ".join(f"`{r.key}`" for r in self.content.all_relics())
            )
        row = await self._ensure(guild_id)
        week = self.current_week()
        if row["relic_key"] == key:
            raise GachaBotError(f"{spec.emoji} **{spec.name}** is already this server's relic.")
        if row["relic_set_at"] == week and row["relic_key"]:
            raise GachaBotError(
                "The relic can only be changed once per week — the forge needs to cool."
            )
        changed = await self.db.execute(
            """
            UPDATE guild_state SET relic_key = :k, relic_set_at = :w
            WHERE guild_id = :g
              AND (relic_key = '' OR relic_set_at IS NULL OR relic_set_at <> :w)
            """,
            {"k": key, "w": week, "g": guild_id},
        )
        if changed == 0:  # a concurrent set won the week's change
            raise GachaBotError(
                "The relic can only be changed once per week — the forge needs to cool."
            )
        self.log.info("guild=%s activated relic %s", guild_id, key)
        await self.bus.publish(
            GameEvent(
                category="guild", action="relic", guild_id=guild_id,
                message=f"relic activated: {spec.emoji} {spec.name} ({spec.flavor})",
            )
        )
        return spec

    # -- contributions & expeditions ---------------------------------------------------

    async def record_hunt(self, guild_id: int | None, user_id: int, coins: int = 0) -> None:
        await self.record(guild_id, user_id, hunts=1, coins=max(coins, 0))

    async def record_pull(self, guild_id: int | None, user_id: int, count: int = 1) -> None:
        await self.record(guild_id, user_id, pulls=count)

    async def record(
        self, guild_id: int | None, user_id: int, *, hunts: int = 0, pulls: int = 0, coins: int = 0
    ) -> None:
        """Record gameplay contributions in ONE transaction.

        Bulk counts exist so batch callers (and tests) don't pay
        per-action round-trips; normal gameplay passes 1.
        """
        if guild_id is None or guild_id <= 0:
            return
        week = self.current_week()
        spec = self.content.expedition_for_week(week)
        rep_gain = 2 * hunts + pulls
        expedition_delta = 0
        if spec is not None:
            expedition_delta = {"hunts": hunts, "pulls": pulls, "coins": coins}.get(spec.metric, 0)
        async with self.db.transaction() as conn:
            await conn.execute(_SQL_UPSERT_GUILD, {"g": guild_id})
            await conn.execute(
                _SQL_UPSERT_CONTRIB,
                {"g": guild_id, "u": user_id, "w": week, "h": hunts, "p": pulls, "c": coins},
            )
            rep_after = (
                await conn.execute(
                    text(
                        "UPDATE guild_state SET reputation = reputation + :d "
                        "WHERE guild_id = :g RETURNING reputation"
                    ),
                    {"d": rep_gain, "g": guild_id},
                )
            ).scalar()
            if spec is not None:
                # week rollover: pin the new week and reset progress (idempotent)
                await conn.execute(
                    text(
                        """
                        UPDATE guild_state
                        SET expedition_week = :w, expedition_key = :k, expedition_progress = 0,
                            expedition_milestones = '', expedition_done = 0
                        WHERE guild_id = :g AND expedition_week <> :w
                        """
                    ),
                    {"w": week, "k": spec.key, "g": guild_id},
                )
                # adopt content changes mid-week (progress preserved), then bump
                await conn.execute(
                    text(
                        "UPDATE guild_state SET expedition_key = :k WHERE guild_id = :g AND expedition_week = :w"
                    ),
                    {"k": spec.key, "g": guild_id, "w": week},
                )
                if expedition_delta > 0:
                    await conn.execute(
                        _SQL_BUMP_EXPEDITION,
                        {"d": expedition_delta, "g": guild_id, "w": week},
                    )
        if rep_gain > 0 and rep_after is not None:
            before_level = self.reputation_level(int(rep_after) - rep_gain)
            after_level = self.reputation_level(int(rep_after))
            if after_level > before_level:
                await self._announce_level(guild_id, after_level)
        await self._evaluate_milestones(guild_id, week)

    async def _announce_level(self, guild_id: int, level: int) -> None:
        self.log.info("guild=%s reached reputation level %d", guild_id, level)
        await self.bus.publish(
            GameEvent(
                category="guild", action="level_up", guild_id=guild_id,
                message=f"guild reached reputation level {level} ({self.level_title(level)})",
            )
        )

    async def _evaluate_milestones(self, guild_id: int, week: str | None = None) -> None:
        week = week or self.current_week()
        row = await self._ensure(guild_id)
        spec = self.content.expedition_for_week(week)
        if spec is None or row["expedition_done"] or row["expedition_week"] != week:
            return
        progress = int(row["expedition_progress"])
        hit = [float(m) for m in row["expedition_milestones"].split(",") if m]
        for milestone in self.content.expedition_milestones:
            if milestone in hit:
                continue
            if progress < spec.target * milestone:
                break  # milestones are ascending; nothing later can pass either
            hit.append(milestone)
            complete = milestone >= 1.0
            # optimistic guard: only one racer may claim a milestone; the
            # expected-list check also blocks replays after completion
            changed = await self.db.execute(
                """
                UPDATE guild_state
                SET expedition_milestones = :m, expedition_done = :d
                WHERE guild_id = :g
                  AND expedition_done = 0
                  AND expedition_milestones = :expected
                """,
                {
                    "m": ",".join(f"{m}" for m in hit), "d": 1 if complete else 0,
                    "g": guild_id, "expected": ",".join(f"{m}" for m in hit[:-1]),
                },
            )
            if changed == 0:
                return  # a concurrent evaluation already claimed it
            if complete:
                await self.add_reputation(guild_id, spec.reputation)
                await self._pay_completion(guild_id, spec)
                self.log.info("guild=%s completed expedition %s", guild_id, spec.key)
                await self.bus.publish(
                    GameEvent(
                        category="guild", action="expedition_complete", guild_id=guild_id,
                        message=f"expedition complete: {spec.emoji} {spec.name} "
                                f"(+{spec.reputation:,} reputation, contributors paid)",
                    )
                )
            else:
                rep_gain = self.content.expedition_milestone_reputation
                await self.add_reputation(guild_id, rep_gain)
                await self.bus.publish(
                    GameEvent(
                        category="guild", action="expedition_milestone", guild_id=guild_id,
                        message=f"expedition milestone {int(milestone * 100)}%: {spec.emoji} {spec.name} "
                                f"(+{rep_gain:,} reputation)",
                    )
                )

    async def _pay_completion(self, guild_id: int, spec: "ExpeditionSpec") -> None:
        coins = self.content.expedition_completion_coins
        rows = await self.db.fetch_all(
            "SELECT user_id FROM guild_contributions WHERE guild_id = :g AND week = :w",
            {"g": guild_id, "w": self.current_week()},
        )
        for row in rows:
            user_id = int(row["user_id"])
            # contributors may never have run a player command — create on demand
            await self.economy.ensure_player(guild_id, user_id)
            await self.economy.deposit(guild_id, user_id, coins, reason=f"expedition:{spec.key}")

    # -- views --------------------------------------------------------------------------

    async def _expedition_for(self, guild_id: int) -> tuple[dict, "ExpeditionSpec | None"]:
        """Read view: rolls the week over lazily if nothing has recorded yet."""
        week = self.current_week()
        row = await self._ensure(guild_id)
        spec = self.content.expedition_for_week(week)
        if row["expedition_week"] != week:
            await self.db.execute(
                """
                UPDATE guild_state
                SET expedition_week = :w, expedition_key = :k, expedition_progress = 0,
                    expedition_milestones = '', expedition_done = 0
                WHERE guild_id = :g
                """,
                {"w": week, "k": spec.key if spec else "", "g": guild_id},
            )
            row = await self._ensure(guild_id)
        return row, spec

    async def expedition_status(self, guild_id: int) -> ExpeditionStatus:
        row, spec = await self._expedition_for(guild_id)
        return ExpeditionStatus(
            spec=spec,
            progress=int(row["expedition_progress"]),
            target=spec.target if spec else 0,
            milestones_hit=tuple(float(m) for m in row["expedition_milestones"].split(",") if m),
            done=bool(row["expedition_done"]),
        )

    async def contributors(self, guild_id: int, limit: int = 5) -> list[dict]:
        rows = await self.db.fetch_all(
            """
            SELECT user_id, hunts, pulls, coins FROM guild_contributions
            WHERE guild_id = :g AND week = :w
            ORDER BY hunts + pulls DESC LIMIT :lim
            """,
            {"g": guild_id, "w": self.current_week(), "lim": limit},
        )
        return [dict(r) for r in rows]

    async def expedition_leaderboard(self, limit: int = 5) -> list[dict]:
        """Cross-guild expedition standings for the current week."""
        rows = await self.db.fetch_all(
            """
            SELECT guild_id, expedition_key, expedition_progress, expedition_done
            FROM guild_state
            WHERE expedition_week = :w AND expedition_progress > 0
            ORDER BY expedition_progress DESC LIMIT :lim
            """,
            {"w": self.current_week(), "lim": limit},
        )
        return [dict(r) for r in rows]
