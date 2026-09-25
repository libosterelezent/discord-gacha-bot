"""Interactive help command (Components V2)."""
from __future__ import annotations

from discord.ext import commands

from bot.ui.help import HelpMenuView


class HelpCog(commands.Cog):
    """\U0001f4d6 Interactive help menu (Components V2)."""

    CATEGORY_EMOJI = "\U0001f4d6"
    CATEGORY_LABEL = "Help"

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="help", description="Interactive help menu with all commands.")
    async def help(self, ctx: commands.Context) -> None:
        await ctx.reply(view=HelpMenuView(self.bot, ctx.clean_prefix), mention_author=False)


async def setup(bot) -> None:
    await bot.add_cog(HelpCog(bot))
