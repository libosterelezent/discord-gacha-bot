"""Equipment commands: list, equip, unequip, upgrade (+level), sell."""
from __future__ import annotations

from typing import Any

from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import SETTINGS
from bot.ui import components as ui
from bot.ui.theme import Theme


def _fmt_piece(content, p: dict[str, Any]) -> str:
    rarity = content.rarity(p["rarity"])
    emoji = rarity.emoji if rarity else "\u26ab"
    template = content.equipment_template(p["item_key"])
    name = template.name if template else p["item_key"].replace("_", " ").title()
    plus = f"+{p['level']}" if p["level"] else ""
    mark = "\U0001f4ce" if p["equipped"] else "\u2022"
    roll = ""
    if template and rarity:
        from bot.models.items import Equipment, EquipmentType

        item = Equipment(
            key=p["item_key"], name=name, etype=EquipmentType.from_key(p["slot"]) or EquipmentType.WEAPON,
            rarity=rarity, attack=p["attack"], defense=p["defense"], luck=p["luck"], level=p["level"],
        )
        pct = content.equipment_roll_percentile(item)
        if pct is not None and pct >= 0.95:
            roll = f" \U0001f525**{pct * 100:.0f}%**"
        elif pct is not None and pct >= 0.9:
            roll = f" ({pct * 100:.0f}%)"
    return (
        f"{mark} `#{p['id']}` {emoji} **{name}{plus}**{roll} "
        f"\u2694{p['attack']} \U0001f6e1{p['defense']} \U0001f380{p['luck']}"
    )


class EquipmentCog(GameMixin):
    """\u2694\ufe0f Equipment management commands."""

    CATEGORY_EMOJI = "\u2694\ufe0f"
    CATEGORY_LABEL = "Equipment"

    @commands.hybrid_command(name="equipment", aliases=["gear", "inv"], description="List your equipment.")
    async def equipment(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        pieces = await self.bot.equipment.owned(player.guild_id, ctx.author.id)
        if not pieces:
            await ctx.reply(
                embed=Theme.error_embed("No equipment yet — pull gacha or hunt for drops!"),
                mention_author=False,
            )
            return
        lines = [_fmt_piece(self.bot.content, p) for p in pieces[:20]]
        if len(pieces) > 20:
            lines.append(f"*...and {len(pieces) - 20} more*")
        note = f"\U0001f4ce = equipped \u00b7 use {ctx.clean_prefix}equip <id> / {ctx.clean_prefix}sellgear <id>"
        await ctx.reply(view=ui.equipment_view(ctx.author.display_name, lines, note), mention_author=False)

    @commands.hybrid_command(name="equip", description="Equip a piece of equipment by ID.")
    async def equip(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player = await self.player(ctx)
        piece = await self.bot.equipment.equip(self.scope_guild(ctx), player, item_id)
        await ctx.reply(ui.plain(f"\U0001f4ce Equipped {_fmt_piece(self.bot.content, piece)}"), mention_author=False)

    @commands.hybrid_command(name="unequip", description="Unequip a piece by ID.")
    async def unequip(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player = await self.player(ctx)
        piece = await self.bot.equipment.unequip(self.scope_guild(ctx), player, item_id)
        await ctx.reply(ui.plain(f"\U0001f4e6 Unequipped `#{piece['id']}` {piece['item_key']}"), mention_author=False)

    @commands.hybrid_command(name="upgequip", aliases=["forge"], description="Upgrade equipment (+1 level).")
    async def upgequip(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player = await self.player(ctx)
        piece, cost = await self.bot.equipment.upgrade(self.scope_guild(ctx), player, item_id)
        await ctx.reply(
            embed=Theme.embed(
                f"\u2692\ufe0f Forged to +{piece['level']}!",
                f"{_fmt_piece(self.bot.content, piece)}\nCost: {SETTINGS.money(cost)}",
                Theme.primary,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="sellgear", description="Sell a piece of equipment for coins.")
    async def sellgear(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player = await self.player(ctx)
        piece, value = await self.bot.equipment.sell(self.scope_guild(ctx), player, item_id)
        await ctx.reply(
            ui.plain(
                f"\U0001f4b1 Sold `#{piece['id']}` {piece['item_key'].replace('_', ' ').title()} for {SETTINGS.money(value)}."
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(EquipmentCog(bot))
