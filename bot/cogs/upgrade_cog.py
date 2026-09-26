"""Upgrade commands: list upgrades, buy levels."""
from __future__ import annotations

from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import SETTINGS
from bot.ui import components as ui
from bot.ui.theme import Theme


class UpgradeCog(GameMixin):
    """\U0001f6e0\ufe0f Permanent player upgrades."""

    CATEGORY_EMOJI = "\U0001f527"
    CATEGORY_LABEL = "Upgrades"

    @commands.hybrid_group(name="upgrades", aliases=["upg", "perks"], invoke_without_command=True)
    async def upgrades(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        lines = []
        for spec in self.bot.content.all_upgrades():
            level = player.upgrades.get(spec.key, 0)
            cost_line = "**MAXED**" if level >= spec.max_level else SETTINGS.money(spec.cost(level))
            lines.append(
                f"{spec.emoji} **{spec.name}** \u00b7 Lv **{level}/{spec.max_level}** \u2014 {spec.description}\n"
                f"\u2003\u21b3 next level: {cost_line}"
            )
        await ctx.reply(
            view=ui.upgrades_view(lines, f"Buy with `{ctx.clean_prefix}upgrades buy <name>`"),
            mention_author=False,
        )

    @upgrades.command(name="buy", description="Buy one level of an upgrade.")
    async def buy(self, ctx: commands.Context, *, name: str) -> None:
        player = await self.player(ctx)
        key = name.strip().lower()
        spec = next(
            (s for s in self.bot.content.all_upgrades() if key in (s.key, s.name.lower())), None
        )
        if spec is None:
            valid = ", ".join(f"`{s.key}`" for s in self.bot.content.all_upgrades())
            await ctx.reply(
                embed=Theme.error_embed(f"Unknown upgrade `{name}`. Available: {valid}"),
                mention_author=False,
            )
            return
        spec, new_level = await self.bot.upgrades.buy(self.scope_guild(ctx), player, spec.key)
        await ctx.reply(
            embed=Theme.embed(
                f"{spec.emoji} {spec.name} \u2192 Level {new_level}!",
                f"Paid {SETTINGS.money(spec.cost(new_level - 1))} \u00b7 {spec.description}",
                Theme.primary,
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(UpgradeCog(bot))
