"""Shared helpers for services: timestamps and common SQL fragments.

Every service previously declared its own ``_now_iso`` and its own copy
of the economy-log INSERT / equipment INSERT / balance UPDATE
statements. One home keeps the SQL dialect-portable and reviewable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text


def now_iso() -> str:
    """UTC timestamp string for storage columns."""
    return datetime.now(timezone.utc).isoformat()


SQL_ADD_BALANCE = text(
    """
    UPDATE players SET balance = balance + :d, updated_at = :t
    WHERE guild_id = :g AND user_id = :u
    """
)

SQL_SUBTRACT_BALANCE_GUARDED = text(
    """
    UPDATE players SET balance = balance - :d, updated_at = :t
    WHERE guild_id = :g AND user_id = :u AND balance >= :d
    """
)

SQL_SELECT_BALANCE = text(
    "SELECT balance FROM players WHERE guild_id = :g AND user_id = :u"
)

SQL_INSERT_ECO_LOG = text(
    """
    INSERT INTO economy_log (guild_id, user_id, delta, reason, balance_after, created_at)
    VALUES (:g, :u, :d, :r, :b, :t)
    """
)

SQL_INSERT_EQUIPMENT = text(
    """
    INSERT INTO equipment (guild_id, user_id, item_key, slot, rarity, level, attack, defense, luck, obtained)
    VALUES (:g, :u, :k, :s, :r, 0, :a, :d, :l, :t)
    RETURNING id
    """
)

SQL_UPSERT_INVENTORY = text(
    """
    INSERT INTO inventory (guild_id, user_id, item_key, quantity) VALUES (:g, :u, :k, 1)
    ON CONFLICT (guild_id, user_id, item_key) DO UPDATE SET quantity = quantity + 1
    """
)
