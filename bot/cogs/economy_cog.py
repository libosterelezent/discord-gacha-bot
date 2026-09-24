"""Economy commands: balance, daily, work, pay, gamble, leaderboard."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.utils.embeds import COLOUR_BAD, COLOUR_GOLD, base_embed, error_embed, money


class EconomyCog(GameMixin):
    """\U0001f4b0 Economy & currency commands."""

    @commands.hybrid_command(name="balance", aliases=["bal", "wallet"], description="Check your coin balance.")
    async def balance(self, ctx: commands.Context) -> None:
        player, profile = await self.player_profile(ctx.author)
        embed = base_embed(
            f"\U0001f4b0 {ctx.author.display_name}'s Wallet",
            f"{money(player.balance)}\n{chr(10024)} Shards: **{player.shards:,}**",
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="daily", description="Claim your daily reward (resets every 24h).")
    async def daily(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        reward = await self.bot.economy.daily(player)
        embed = base_embed(
            "\U0001f381 Daily Reward Claimed!",
            f"You received {money(reward)}.\nCome back tomorrow for more!",
            COLOUR_GOLD,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="work", description="Work for coins (hourly).")
    async def work(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        earned = await self.bot.economy.work(player)
        embed = base_embed(
            "\U0001f4bc Hard Day's Work",
            f"You earned {money(earned)}.",
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="pay", aliases=["give"], description="Send coins to another player.")
    async def pay(self, ctx: commands.Context, member: discord.Member, amount: app_commands.Range[int, 1]) -> None:
        player, _ = await self.player_profile(ctx.author)
        new_balance = await self.bot.economy.transfer(player, member.id, amount)
        embed = base_embed(
            "\U0001f48c Transfer Complete",
            f"{ctx.author.mention} \u2192 {member.mention}: {money(amount)}\nYour balance: {money(new_balance)}",
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="gamble", description="50/50 double or nothing.")
    async def gamble(self, ctx: commands.Context, amount: app_commands.Range[int, 1]) -> None:
        player, _ = await self.player_profile(ctx.author)
        delta = await self.bot.economy.gamble(player, amount)
        if delta > 0:
            embed = base_embed(
                "\U0001f0cf You Won!",
                f"+{money(delta)}\nNew balance: {money(player.balance)}",
                discord.Colour.green(),
            )
        else:
            embed = base_embed(
                "\U0001f0b2 You Lost...",
                f"{money(delta)}\nNew balance: {money(player.balance)}",
                COLOUR_BAD,
            )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"], description="Top 10 richest players.")
    async def leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.bot.economy.leaderboard(10)
        if not rows:
            await ctx.reply(embed=error_embed("No players yet — be the first!"), mention_author=False)
            return
        medals = ("\U0001f947", "\U0001f948", "\U0001f949")
        lines = []
        for i, (user_id, bal, level) in enumerate(rows, 1):
            member = ctx.guild.get_member(user_id) if ctx.guild else None
            name = member.display_name if member else f"User {user_id}"
            prefix = medals[i - 1] if i <= 3 else f"`{i}.`"
            lines.append(f"{prefix} **{name}** \u2014 {bal:,} \u00b7 Lv.{level}")
        embed = base_embed("\U0001f3c6 Wealthiest Hunters", "\n".join(lines), COLOUR_GOLD)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot) -> None:
    await bot.add_cog(EconomyCog(bot))
