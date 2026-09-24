"""Equipment service: inventory ops, equipping, item upgrades, selling.

Equipment upgrade cost scales with rarity tier and target level:
``cost = 250 * rarity^1.5 * (level + 1)^1.6``
"""
from __future__ import annotations

from bot.core.database import Database
from bot.core.exceptions import EquipmentError, InsufficientFundsError, ItemNotFoundError, NotEnoughItemsError
from bot.models.player import Player
from bot.models.rarities import Rarity
from bot.services.base import BaseService

SLOT_KEYS: tuple[str, ...] = ("weapon", "armor", "amulet")


class EquipmentService(BaseService):
    log_name = "gacha.equipment"

    async def on_start(self) -> None:
        self.log.info("Equipment service ready")

    # -- queries ----------------------------------------------------------------

    async def owned(self, user_id: int) -> list[dict]:
        rows = await self.db.fetch_all(
            """
            SELECT id, item_key, slot, rarity, level, attack, defense, luck, equipped
            FROM equipment WHERE user_id = ? ORDER BY equipped DESC, attack * 2 + defense * 2 + luck * 3 DESC
            """,
            (str(user_id),),
        )
        return [dict(r) for r in rows]

    async def get_piece(self, user_id: int, equipment_id: int) -> dict:
        row = await self.db.fetch_one(
            "SELECT * FROM equipment WHERE id = ? AND user_id = ?", (equipment_id, str(user_id))
        )
        if row is None:
            raise ItemNotFoundError(f"equipment#{equipment_id}")
        return dict(row)

    async def equipped(self, user_id: int) -> dict[str, dict]:
        rows = await self.db.fetch_all(
            "SELECT * FROM equipment WHERE user_id = ? AND equipped = 1", (str(user_id),)
        )
        return {r["slot"]: dict(r) for r in rows}

    # -- mutations -----------------------------------------------------------------

    async def equip(self, player: Player, equipment_id: int) -> dict:
        piece = await self.get_piece(player.user_id, equipment_id)
        if piece["equipped"]:
            raise EquipmentError("That piece is already equipped.")
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE equipment SET equipped = 0 WHERE user_id = ? AND slot = ?",
                (str(player.user_id), piece["slot"]),
            )
            await conn.execute(
                "UPDATE equipment SET equipped = 1 WHERE id = ? AND user_id = ?",
                (piece["id"], str(player.user_id)),
            )
        self.log.info("user=%s equipped #%d (%s)", player.user_id, piece["id"], piece["item_key"])
        piece["equipped"] = 1
        return piece

    async def unequip(self, player: Player, equipment_id: int) -> dict:
        piece = await self.get_piece(player.user_id, equipment_id)
        if not piece["equipped"]:
            raise EquipmentError("That piece is not equipped.")
        await self.db.execute(
            "UPDATE equipment SET equipped = 0 WHERE id = ? AND user_id = ?",
            (piece["id"], str(player.user_id)),
        )
        piece["equipped"] = 0
        return piece

    def upgrade_cost(self, piece: dict) -> int:
        rarity = Rarity.from_key(piece["rarity"]) or Rarity.COMMON
        return int(250 * (rarity.value ** 1.5) * ((piece["level"] + 1) ** 1.6))

    async def upgrade(self, player: Player, equipment_id: int) -> tuple[dict, int]:
        piece = await self.get_piece(player.user_id, equipment_id)
        if piece["level"] >= 10:
            raise EquipmentError("Equipment can reach a maximum of +10.")
        cost = self.upgrade_cost(piece)
        if player.balance < cost:
            raise InsufficientFundsError(cost, player.balance)
        growth = 1.12
        new_atk = round(piece["attack"] * growth)
        new_dfn = round(piece["defense"] * growth)
        new_luk = round(piece["luck"] * growth)
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (cost, str(player.user_id)),
            )
            await conn.execute(
                """
                UPDATE equipment
                SET level = level + 1, attack = ?, defense = ?, luck = ?
                WHERE id = ? AND user_id = ?
                """,
                (new_atk, new_dfn, new_luk, piece["id"], str(player.user_id)),
            )
            await conn.execute(
                "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                (str(player.user_id), -cost, f"equip_upgrade:{piece['id']}", player.balance - cost),
            )
        player.balance -= cost
        piece.update(level=piece["level"] + 1, attack=new_atk, defense=new_dfn, luck=new_luk)
        self.log.info("user=%s upgraded equipment #%d -> +%d", player.user_id, piece["id"], piece["level"])
        return piece, cost

    async def sell(self, player: Player, equipment_id: int) -> tuple[dict, int]:
        piece = await self.get_piece(player.user_id, equipment_id)
        rarity = Rarity.from_key(piece["rarity"]) or Rarity.COMMON
        lo, hi = rarity.value_range
        value = int((lo + hi) // 2 * (1 + 0.15 * piece["level"]))
        async with self.db.transaction() as conn:
            await conn.execute(
                "DELETE FROM equipment WHERE id = ? AND user_id = ?", (piece["id"], str(player.user_id))
            )
            await conn.execute(
                "UPDATE players SET balance = balance + ?, updated_at = datetime('now') WHERE user_id = ?",
                (value, str(player.user_id)),
            )
            await conn.execute(
                "INSERT INTO economy_log (user_id, delta, reason, balance_after) VALUES (?, ?, ?, ?)",
                (str(player.user_id), value, f"equip_sell:{piece['id']}", player.balance + value),
            )
        player.balance += value
        self.log.info("user=%s sold equipment #%d (+%d)", player.user_id, piece["id"], value)
        return piece, value

    async def card_count(self, user_id: int, item_key: str) -> int:
        val = await self.db.fetch_val(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_key = ?",
            (str(user_id), item_key),
        )
        return int(val or 0)

    async def consume_cards(self, player: Player, item_key: str, amount: int) -> None:
        """Fails with NotEnoughItemsError unless at least `amount` copies exist."""
        have = await self.card_count(player.user_id, item_key)
        if have < amount:
            raise NotEnoughItemsError()
        await self.db.execute(
            "UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND item_key = ?",
            (amount, str(player.user_id), item_key),
        )
