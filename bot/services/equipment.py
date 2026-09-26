"""Equipment service: inventory ops, equipping, item upgrades, selling.

Equipment upgrade cost scales with rarity tier and target level:
``cost = base * tier^1.5 * (level + 1)^1.6`` — all knobs in game.json.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from bot.core.events import GameEvent
from bot.core.exceptions import EquipmentError, InsufficientFundsError, ItemNotFoundError, NotEnoughItemsError
from bot.core.util import SQL_ADD_BALANCE, SQL_INSERT_ECO_LOG, SQL_SUBTRACT_BALANCE_GUARDED, now_iso
from bot.models.player import Player
from bot.services.base import BaseService


SLOT_KEYS: tuple[str, ...] = ("weapon", "armor", "amulet")


def _now_iso() -> str:
    return now_iso()


_SQL_SPEND = SQL_SUBTRACT_BALANCE_GUARDED
_SQL_GAIN = SQL_ADD_BALANCE
_SQL_ECO_LOG = SQL_INSERT_ECO_LOG


class EquipmentService(BaseService):
    log_name = "gacha.equipment"

    async def on_start(self) -> None:
        self.log.info("Equipment service ready")

    # -- queries ----------------------------------------------------------------

    async def owned(self, guild_id: int, user_id: int) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            """
            SELECT id, item_key, slot, rarity, level, attack, defense, luck, equipped
            FROM equipment
            WHERE guild_id = :g AND user_id = :u
            ORDER BY equipped DESC, attack * 2 + defense * 2 + luck * 3 DESC
            """,
            {"g": guild_id, "u": user_id},
        )
        return [dict(r) for r in rows]

    async def get_piece(self, guild_id: int, user_id: int, equipment_id: int) -> dict[str, Any]:
        row = await self.db.fetch_one(
            "SELECT * FROM equipment WHERE id = :id AND guild_id = :g AND user_id = :u",
            {"id": equipment_id, "g": guild_id, "u": user_id},
        )
        if row is None:
            raise ItemNotFoundError(f"equipment#{equipment_id}")
        return dict(row)

    async def equipped(self, guild_id: int, user_id: int) -> dict[str, dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT * FROM equipment WHERE guild_id = :g AND user_id = :u AND equipped = 1",
            {"g": guild_id, "u": user_id},
        )
        return {r["slot"]: dict(r) for r in rows}

    # -- mutations -----------------------------------------------------------------

    async def equip(self, guild_id: int | None, player: Player, equipment_id: int) -> dict[str, Any]:
        piece = await self.get_piece(player.guild_id, player.user_id, equipment_id)
        if piece["equipped"]:
            raise EquipmentError("That piece is already equipped.")
        async with self.db.transaction() as conn:
            await conn.execute(
                text("UPDATE equipment SET equipped = 0 WHERE guild_id = :g AND user_id = :u AND slot = :s"),
                {"g": player.guild_id, "u": player.user_id, "s": piece["slot"]},
            )
            await conn.execute(
                text("UPDATE equipment SET equipped = 1 WHERE id = :id"),
                {"id": piece["id"]},
            )
        self.log.info("user=%s equipped #%d (%s)", player.user_id, piece["id"], piece["item_key"])
        await self.bus.publish(
            GameEvent(
                category="equipment", action="equip", guild_id=guild_id, user_id=player.user_id,
                message=f"equipped {piece['item_key']} #{piece['id']}",
            )
        )
        piece["equipped"] = 1
        return piece

    async def unequip(self, guild_id: int | None, player: Player, equipment_id: int) -> dict[str, Any]:
        piece = await self.get_piece(player.guild_id, player.user_id, equipment_id)
        if not piece["equipped"]:
            raise EquipmentError("That piece is not equipped.")
        await self.db.execute(
            "UPDATE equipment SET equipped = 0 WHERE id = :id", {"id": piece["id"]}
        )
        piece["equipped"] = 0
        return piece

    def upgrade_cost(self, piece: dict[str, Any]) -> int:
        rarity = self.content.rarity(piece["rarity"])
        tier = rarity.tier if rarity else 1
        base = self.settings.equipment.upgrade_cost_base
        return int(base * (tier ** 1.5) * ((piece["level"] + 1) ** 1.6))

    async def upgrade(self, guild_id: int | None, player: Player, equipment_id: int) -> tuple[dict[str, Any], int]:
        piece = await self.get_piece(player.guild_id, player.user_id, equipment_id)
        if piece["level"] >= self.settings.equipment.max_level:
            raise EquipmentError(f"Equipment can reach a maximum of +{self.settings.equipment.max_level}.")
        cost = self.upgrade_cost(piece)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)
        growth = 1 + self.settings.equipment.forge_growth
        new_atk = round(piece["attack"] * growth)
        new_dfn = round(piece["defense"] * growth)
        new_luk = round(piece["luck"] * growth)
        now = _now_iso()
        async with self.db.transaction() as conn:
            spend = await conn.execute(_SQL_SPEND, {"d": cost, "t": now, "g": player.guild_id, "u": player.user_id})
            if spend.rowcount == 0:  # concurrent spend won the funds
                raise InsufficientFundsError(cost, player.balance)
            await conn.execute(
                text("UPDATE equipment SET level = level + 1, attack = :a, defense = :d, luck = :l WHERE id = :id"),
                {"a": new_atk, "d": new_dfn, "l": new_luk, "id": piece["id"]},
            )
            await conn.execute(
                _SQL_ECO_LOG,
                {"g": player.guild_id, "u": player.user_id, "d": -cost, "r": f"equip_upgrade:{piece['id']}", "b": player.balance - cost, "t": now},
            )
        player.balance -= cost
        piece.update(level=piece["level"] + 1, attack=new_atk, defense=new_dfn, luck=new_luk)
        self.log.info("user=%s upgraded equipment #%d -> +%d", player.user_id, piece["id"], piece["level"])
        await self.bus.publish(
            GameEvent(
                category="equipment", action="forge", guild_id=guild_id, user_id=player.user_id,
                message=f"forged {piece['item_key']} #{piece['id']} to +{piece['level']} (-{cost:,})",
            )
        )
        return piece, cost

    async def sell(self, guild_id: int | None, player: Player, equipment_id: int) -> tuple[dict[str, Any], int]:
        piece = await self.get_piece(player.guild_id, player.user_id, equipment_id)
        rarity = self.content.rarity(piece["rarity"])
        if rarity is None:
            raise ItemNotFoundError(f"equipment#{equipment_id}")
        value = int(rarity.sell_value * (1 + self.settings.equipment.sell_level_bonus * piece["level"]))
        now = _now_iso()
        async with self.db.transaction() as conn:
            await conn.execute(
                text("DELETE FROM equipment WHERE id = :id"), {"id": piece["id"]}
            )
            await conn.execute(_SQL_GAIN, {"d": value, "t": now, "g": player.guild_id, "u": player.user_id})
            await conn.execute(
                _SQL_ECO_LOG,
                {"g": player.guild_id, "u": player.user_id, "d": value, "r": f"equip_sell:{piece['id']}", "b": player.balance + value, "t": now},
            )
        player.balance += value
        self.log.info("user=%s sold equipment #%d (+%d)", player.user_id, piece["id"], value)
        await self.bus.publish(
            GameEvent(
                category="equipment", action="sell", guild_id=guild_id, user_id=player.user_id,
                message=f"sold {piece['item_key']} #{piece['id']} for {value:,}",
            )
        )
        return piece, value

    # -- card inventory helpers ------------------------------------------------------

    async def card_count(self, guild_id: int, user_id: int, item_key: str) -> int:
        val = await self.db.fetch_val(
            "SELECT quantity FROM inventory WHERE guild_id = :g AND user_id = :u AND item_key = :k",
            {"g": guild_id, "u": user_id, "k": item_key},
        )
        return int(val or 0)

    async def consume_cards(self, player: Player, item_key: str, amount: int) -> None:
        """Fails with NotEnoughItemsError unless at least `amount` copies exist."""
        have = await self.card_count(player.guild_id, player.user_id, item_key)
        if have < amount:
            raise NotEnoughItemsError()
        await self.db.execute(
            "UPDATE inventory SET quantity = quantity - :a WHERE guild_id = :g AND user_id = :u AND item_key = :k",
            {"a": amount, "g": player.guild_id, "u": player.user_id, "k": item_key},
        )
