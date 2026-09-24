"""Admin/owner commands: grant currency, inspect database statistics."""
from __future__ import annotations

import platform
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.utils.embeds import base_embed, money


def is_owner() -> commands.check:
    async def predicate(ctx: commands.Context) -> bool:
        app = await ctx.bot.application_info()
        return ctx.author.id == app.owner.id
    return commands.check(predicate)


class AdminCog(GameMixin):
    """\U0001f6e1\ufe0f Owner-only maintenance commands."""

    @commands.hybrid_command(name="grant", description="[OWNER] Grant coins to a player.")
    @is_owner()
    @commands.guild_only()
    async def grant(self, ctx: commands.Context, member: discord.Member, amount: app_commands.Range[int, 1]) -> None:
        new_balance = await self.bot.economy.deposit(member.id, amount, reason=f"grant:{ctx.author.id}")
        await ctx.reply(
            embed=base_embed("\U0001f4b0 Granted", f"{member.mention} received {money(amount)} (now {money(new_balance)})."),
            mention_author=False,
        )

    @commands.hybrid_command(name="botstats", description="[OWNER] Runtime & database statistics.")
    @is_owner()
    async def botstats(self, ctx: commands.Context) -> None:
        db = self.bot.db
        players = await db.fetch_val("SELECT COUNT(*) FROM players")
        pulls = await db.fetch_val("SELECT COALESCE(SUM(total_pulls), 0) FROM players")
        coins = await db.fetch_val("SELECT COALESCE(SUM(balance), 0) FROM players")
        equipment = await db.fetch_val("SELECT COUNT(*) FROM equipment")
        bots = await db.fetch_val("SELECT COUNT(*) FROM huntbots WHERE active = 1")
        uptime = time.time() - _START_TIME
        embed = base_embed(
            "\U0001f4ca Bot Statistics",
            (
                f"Servers: **{len(self.bot.guilds)}**\n"
                f"Players: **{players}**\n"
                f"Total pulls: **{pulls:,}**\n"
                f"Coins in circulation: **{coins:,}**\n"
                f"Equipment pieces: **{equipment:,}**\n"
                f"Active huntbots: **{bots}**\n"
                f"Uptime: **{uptime // 3600:.0f}h {uptime % 3600 // 60:.0f}m**\n"
                f"Python: **{platform.python_version()}** \u00b7 discord.py **{discord.__version__}**"
            ),
        )
        await ctx.reply(embed=embed, mention_author=False)


_START_TIME = time.time()


async def setup(bot) -> None:
    await bot.add_cog(AdminCog(bot))
