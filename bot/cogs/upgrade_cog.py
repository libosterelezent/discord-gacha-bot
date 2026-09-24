"""Upgrade commands: list upgrades, buy levels."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.services.upgrades import UpgradeCatalog
from bot.utils.embeds import COLOUR_GOLD, base_embed, error_embed, money


class UpgradeCog(GameMixin):
    """\U0001f6e0\ufe0f Permanent player upgrades."""

    @commands.hybrid_group(name="upgrades", aliases=["upg", "perks"], invoke_without_command=True)
    async def upgrades(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        lines = []
        for spec in UpgradeCatalog.all():
            level = player.upgrades.get(spec.key, 0)
            if level >= spec.max_level:
                cost_line = "**MAXED**"
            else:
                cost_line = money(spec.cost(level))
            lines.append(
                f"{spec.emoji} **{spec.name}** \u00b7 Lv **{level}/{spec.max_level}** \u2014 {spec.description}\n"
                f"\u2003\u21b3 next level: {cost_line}"
            )
        embed = base_embed(
            "\U0001f6e0\ufe0f Upgrade Workshop",
            "\n".join(lines) + f"\n\nBuy with `{ctx.clean_prefix}upgrades buy <name>`",
            COLOUR_GOLD,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @upgrades.command(name="buy", description="Buy one level of an upgrade.")
    async def buy(self, ctx: commands.Context, *, name: str) -> None:
        player, _ = await self.player_profile(ctx.author)
        key = name.strip().lower()
        spec = next(
            (s for s in UpgradeCatalog.all() if key in (s.key, s.name.lower())), None
        )
        if spec is None:
            valid = ", ".join(f"`{s.key}`" for s in UpgradeCatalog.all())
            await ctx.reply(
                embed=error_embed(f"Unknown upgrade `{name}`. Available: {valid}"),
                mention_author=False,
            )
            return
        new_level, cost = await self.bot.upgrades.buy(player, spec.key)
        await ctx.reply(
            embed=base_embed(
                f"{spec.emoji} {spec.name} \u2192 Level {new_level}!",
                f"Paid {money(cost)} \u00b7 {spec.description}",
                COLOUR_GOLD,
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(UpgradeCog(bot))
