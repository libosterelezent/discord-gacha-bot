"""Economy commands: balance, daily, work, pay, gamble, leaderboard."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import SETTINGS
from bot.ui import components as ui
from bot.ui.theme import Theme


class EconomyCog(GameMixin):
    """\U0001f4b0 Economy & currency commands."""

    CATEGORY_EMOJI = "\U0001fa99"
    CATEGORY_LABEL = "Economy"

    @commands.hybrid_command(name="balance", aliases=["bal", "wallet"], description="Check your coin balance.")
    async def balance(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        await ctx.reply(
            ui.plain(
                f"{SETTINGS.currency.emoji} **{ctx.author.display_name}** — "
                f"**{player.balance:,}** {SETTINGS.currency.name.lower()} \u00b7 "
                f"{SETTINGS.shards.emoji} **{player.shards:,}** {SETTINGS.shards.name.lower()}"
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="daily", description="Claim your daily reward (resets every 24h).")
    async def daily(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        reward = await self.bot.economy.daily(self.scope_guild(ctx), player)
        await ctx.reply(
            embed=Theme.embed(
                "\U0001f381 Daily Reward Claimed!",
                f"You received {SETTINGS.money(reward)}.\nCome back tomorrow for more!",
                Theme.success,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="work", description="Work for coins (hourly).")
    async def work(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        earned = await self.bot.economy.work(self.scope_guild(ctx), player)
        await ctx.reply(
            ui.plain(f"\U0001f4bc You worked a shift and earned {SETTINGS.money(earned)}."),
            mention_author=False,
        )

    @commands.hybrid_command(name="pay", aliases=["give"], description="Send coins to another player.")
    @commands.guild_only()
    async def pay(self, ctx: commands.Context, member: discord.Member, amount: commands.Range[int, 1]) -> None:
        player = await self.player(ctx)
        new_balance = await self.bot.economy.transfer(self.scope_guild(ctx), player, member.id, amount)
        await ctx.reply(
            ui.plain(
                f"\U0001f48c {ctx.author.mention} \u2192 {member.mention}: {SETTINGS.money(amount)} "
                f"(new balance: {SETTINGS.money(new_balance)})"
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="gamble", description="50/50 double or nothing.")
    async def gamble(self, ctx: commands.Context, amount: commands.Range[int, 1]) -> None:
        player = await self.player(ctx)
        delta = await self.bot.economy.gamble(self.scope_guild(ctx), player, amount)
        if delta > 0:
            await self.bot.records.submit_max(
                self.scope_guild(ctx), "gamble_best", "Biggest gamble win",
                ctx.author.id, delta,
            )
            await ctx.reply(
                embed=Theme.embed("\U0001f0cf You Won!", f"+{SETTINGS.money(delta)}\nNew balance: {SETTINGS.money(player.balance)}", Theme.success),
                mention_author=False,
            )
        else:
            await ctx.reply(
                embed=Theme.embed("\U0001f0b2 You Lost...", f"{SETTINGS.money(delta)}\nNew balance: {SETTINGS.money(player.balance)}", Theme.error),
                mention_author=False,
            )

    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"], description="Top 10 richest players.")
    async def leaderboard(self, ctx: commands.Context, scope: str = "guild") -> None:
        scope = scope.lower()
        if scope not in ("guild", "global"):
            scope = "guild"
        rows = await self.bot.economy.leaderboard(self.scope_guild(ctx), 10, scope=scope)  # type: ignore[arg-type]
        if not rows:
            await ctx.reply(embed=Theme.error_embed("No players yet — be the first!"), mention_author=False)
            return
        medals = ("\U0001f947", "\U0001f948", "\U0001f949")
        lines = []
        for i, (user_id, bal, level) in enumerate(rows, 1):
            member = ctx.guild.get_member(user_id) if ctx.guild else None
            name = member.display_name if member else f"User {user_id}"
            prefix = medals[i - 1] if i <= 3 else f"`{i}.`"
            lines.append(f"{prefix} **{name}** \u2014 {bal:,} \u00b7 Lv.{level}")
        title = f" Wealthiest Hunters ({scope})"
        await ctx.reply(view=ui.leaderboard_view(title.strip(), lines), mention_author=False)


async def setup(bot) -> None:
    await bot.add_cog(EconomyCog(bot))
