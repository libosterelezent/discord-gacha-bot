"""Equipment commands: list, equip, unequip, upgrade (+level), sell."""
from __future__ import annotations

from typing import Any

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.models.rarities import Rarity
from bot.utils.embeds import COLOUR_GOLD, base_embed, error_embed, money


def _fmt_piece(p: dict[str, Any]) -> str:
    rarity = Rarity.from_key(p["rarity"]) or Rarity.COMMON
    plus = f"+{p['level']}" if p["level"] else ""
    mark = "\U0001f4ce" if p["equipped"] else "\u2022"
    return (
        f"{mark} `#{p['id']}` {rarity.emoji} **{p['item_key'].replace('_', ' ').title()}{plus}** "
        f"\u2694{p['attack']} \U0001f6e1{p['defense']} \U0001f380{p['luck']}"
    )


class EquipmentCog(GameMixin):
    """\u2694\ufe0f Equipment management commands."""

    @commands.hybrid_command(name="equipment", aliases=["gear", "inv"], description="List your equipment.")
    async def equipment(self, ctx: commands.Context) -> None:
        pieces = await self.bot.equipment.owned(ctx.author.id)
        if not pieces:
            await ctx.reply(
                embed=error_embed("No equipment yet — pull gacha or hunt for drops!"),
                mention_author=False,
            )
            return
        lines = [_fmt_piece(p) for p in pieces[:20]]
        embed = base_embed(
            f"\U0001f392 {ctx.author.display_name}'s Equipment",
            "\n".join(lines) + (f"\n*...and {len(pieces) - 20} more*" if len(pieces) > 20 else ""),
            COLOUR_GOLD,
        )
        embed.set_footer(text="\U0001f4ce = equipped \u00b7 use !equip <id> / !sellgear <id>")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="equip", description="Equip a piece of equipment by ID.")
    async def equip(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player, _ = await self.player_profile(ctx.author)
        piece = await self.bot.equipment.equip(player, item_id)
        await ctx.reply(
            embed=base_embed("\U0001f4ce Equipped", _fmt_piece(piece)),
            mention_author=False,
        )

    @commands.hybrid_command(name="unequip", description="Unequip a piece by ID.")
    async def unequip(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player, _ = await self.player_profile(ctx.author)
        piece = await self.bot.equipment.unequip(player, item_id)
        await ctx.reply(
            embed=base_embed("\U0001f4e6 Unequipped", f"`#{piece['id']}` {piece['item_key']}"),
            mention_author=False,
        )

    @commands.hybrid_command(name="upgequip", aliases=["forge"], description="Upgrade equipment (+1 level).")
    async def upgequip(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player, _ = await self.player_profile(ctx.author)
        piece, cost = await self.bot.equipment.upgrade(player, item_id)
        await ctx.reply(
            embed=base_embed(
                f"\u2692\ufe0f Forged to +{piece['level']}!",
                f"{_fmt_piece(piece)}\nCost: {money(cost)}",
                COLOUR_GOLD,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="sellgear", description="Sell a piece of equipment for coins.")
    async def sellgear(self, ctx: commands.Context, item_id: commands.Range[int, 1]) -> None:
        player, _ = await self.player_profile(ctx.author)
        piece, value = await self.bot.equipment.sell(player, item_id)
        await ctx.reply(
            embed=base_embed(
                "\U0001f4b1 Sold",
                f"`#{piece['id']}` {piece['item_key'].replace('_', ' ').title()} for {money(value)}.",
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(EquipmentCog(bot))
