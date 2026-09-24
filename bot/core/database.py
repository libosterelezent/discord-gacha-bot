"""Asynchronous database layer built on aiosqlite.

Highlights
----------
* Single shared connection guarded by an `asyncio.Lock` (SQLite is
  single-writer; serialising access avoids `database is locked`).
* `async with db.transaction():` — an async context manager giving
  automatic COMMIT on success and ROLLBACK on any exception.
* Schema migrations via a versioned, ordered list of migration callables.
* Typed helpers returning `sqlite3.Row` (mapping access) rather than
  bare tuples.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence

import aiosqlite

from bot.core.exceptions import DatabaseError

logger = logging.getLogger("gacha.database")

Row = aiosqlite.Row

SCHEMA_VERSION: int = 1


async def _migration_v1(db: aiosqlite.Connection) -> None:
    """Version 1 — initial schema."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
            user_id     TEXT PRIMARY KEY,
            balance     INTEGER NOT NULL DEFAULT 0,
            xp          INTEGER NOT NULL DEFAULT 0,
            level       INTEGER NOT NULL DEFAULT 1,
            total_pulls INTEGER NOT NULL DEFAULT 0,
            pity_counter INTEGER NOT NULL DEFAULT 0,
            shards      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS inventory (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL REFERENCES players(user_id) ON DELETE CASCADE,
            item_key   TEXT NOT NULL,
            quantity   INTEGER NOT NULL DEFAULT 1,
            UNIQUE(user_id, item_key)
        );

        CREATE TABLE IF NOT EXISTS equipment (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL REFERENCES players(user_id) ON DELETE CASCADE,
            item_key  TEXT NOT NULL,
            slot      TEXT NOT NULL,
            rarity    TEXT NOT NULL,
            level     INTEGER NOT NULL DEFAULT 0,
            attack    INTEGER NOT NULL DEFAULT 0,
            defense   INTEGER NOT NULL DEFAULT 0,
            luck      INTEGER NOT NULL DEFAULT 0,
            equipped  INTEGER NOT NULL DEFAULT 0,
            obtained  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_equipment_user ON equipment(user_id, equipped);

        CREATE TABLE IF NOT EXISTS upgrades (
            user_id     TEXT NOT NULL REFERENCES players(user_id) ON DELETE CASCADE,
            upgrade_key TEXT NOT NULL,
            level       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, upgrade_key)
        );

        CREATE TABLE IF NOT EXISTS huntbots (
            user_id        TEXT PRIMARY KEY REFERENCES players(user_id) ON DELETE CASCADE,
            level          INTEGER NOT NULL DEFAULT 1,
            active         INTEGER NOT NULL DEFAULT 0,
            battery        INTEGER NOT NULL DEFAULT 0,
            last_tick      TEXT,
            unclaimed_coins INTEGER NOT NULL DEFAULT 0,
            unclaimed_items TEXT NOT NULL DEFAULT '',
            hunts_done     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS economy_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            delta      INTEGER NOT NULL,
            reason     TEXT NOT NULL,
            balance_after INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_economy_log_user ON economy_log(user_id);

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );
        """
    )
    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


MIGRATIONS: tuple[tuple[int, Callable[[aiosqlite.Connection], Any]], ...] = ((1, _migration_v1),)


class Database:
    """Async facade over an SQLite database file."""

    __slots__ = ("_path", "_conn", "_lock", "_ready")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._ready = False

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open the connection, enable pragmas, and run pending migrations."""
        if self._ready:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = await aiosqlite.connect(self._path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL;")
            await self._conn.execute("PRAGMA foreign_keys=ON;")
            await self._conn.execute("PRAGMA synchronous=NORMAL;")
            await self._migrate()
            await self._conn.commit()
        except Exception as exc:
            logger.exception("Failed to connect/migrate database at %s", self._path)
            raise DatabaseError("Database initialisation failed.", original=exc) from exc
        self._ready = True
        logger.info("Database ready at %s (schema v%d)", self._path, SCHEMA_VERSION)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._ready = False
            logger.info("Database connection closed.")

    async def _migrate(self) -> None:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        exists = await cur.fetchone()
        current = 0
        if exists:
            cur = await self._conn.execute("SELECT MAX(version) AS v FROM schema_version")
            row = await cur.fetchone()
            current = int(row["v"]) if row and row["v"] is not None else 0

        for version, migrate in MIGRATIONS:
            if version > current:
                logger.info("Applying migration v%d ...", version)
                await migrate(self._conn)

    # -- transaction support -------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Async context manager: COMMIT on success, ROLLBACK on error."""
        if self._conn is None:
            raise DatabaseError("Database not connected.")
        async with self._lock:
            try:
                yield self._conn
                await self._conn.commit()
            except Exception as exc:
                await self._conn.rollback()
                logger.error("Transaction rolled back: %s", exc, exc_info=exc)
                raise
        # lock released

    # -- convenience query helpers --------------------------------------------

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a single statement inside its own transaction; return rowcount."""
        async with self.transaction() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Row | None:
        if self._conn is None:
            raise DatabaseError("Database not connected.")
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            return await cur.fetchone()

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Row]:
        if self._conn is None:
            raise DatabaseError("Database not connected.")
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            rows = await cur.fetchall()
            return list(rows)

    async def fetch_val(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = await self.fetch_one(sql, params)
        return row[0] if row is not None else None
