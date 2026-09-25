"""Admin/owner commands: grant currency, badges, log channels, bot stats,
settings reload — everything a maintainer needs to operate the game.
"""
from __future__ import annotations

import platform
import time

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import CONFIG, SETTINGS
from bot.observability.discord_sink import VALID_CATEGORIES
from bot.ui.theme import Theme


def is_owner() -> commands.check:
    async def predicate(ctx: commands.Context) -> bool:
        app = await ctx.bot.application_info()
        return ctx.author.id == app.owner.id
    return commands.check(predicate)


_START_TIME = time.time()


class AdminCog(GameMixin):
    """\U0001f6e1\ufe0f Owner-only maintenance commands."""

    CATEGORY_EMOJI = "\U0001f6e1\ufe0f"
    CATEGORY_LABEL = "Admin"

    @commands.hybrid_command(name="grant", description="[OWNER] Grant coins to a player.")
    @is_owner()
    @commands.guild_only()
    async def grant(self, ctx: commands.Context, member: discord.Member, amount: commands.Range[int, 1]) -> None:
        new_balance = await self.bot.economy.deposit(
            ctx.guild.id, member.id, amount, reason=f"grant:{ctx.author.id}"
        )
        await ctx.reply(
            embed=Theme.embed("\U0001f4b0 Granted", f"{member.mention} received {SETTINGS.money(amount)} (now {SETTINGS.money(new_balance)}).", Theme.success),
            mention_author=False,
        )

    @commands.hybrid_group(name="badges", invoke_without_command=True, description="[OWNER] Badge management.")
    @is_owner()
    @commands.guild_only()
    async def badges(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        """List badge definitions, or a player's badges."""
        if member is None:
            lines = [
                f"{b.emoji} **{b.name}** (`{b.key}`) \u00b7 {b.scope} \u2014 {b.description}"
                for b in self.bot.content.all_badges()
            ]
            await ctx.reply(
                embed=Theme.embed("\U0001f3c5 Badge Definitions", "\n".join(lines) or "*None defined.*"),
                mention_author=False,
            )
            return
        owned = await self.bot.badges.player_badges(member.id, ctx.guild.id)
        lines = [f"{spec.emoji} **{spec.name}** ({'global' if g is None else 'guild'})" for spec, g in owned]
        await ctx.reply(
            embed=Theme.embed(f"\U0001f3c5 {member.display_name}'s Badges", "\n".join(lines) or "*None yet.*"),
            mention_author=False,
        )

    @badges.command(name="grant", description="[OWNER] Award a badge to a player.")
    @is_owner()
    @commands.guild_only()
    async def badges_grant(self, ctx: commands.Context, member: discord.Member, badge_key: str) -> None:
        spec = await self.bot.badges.grant(ctx.guild.id, member.id, badge_key.lower(), granted_by=ctx.author.id)
        await ctx.reply(
            embed=Theme.embed("\U0001f3c5 Badge Awarded", f"{member.mention} received {spec.emoji} **{spec.name}** ({spec.scope}).", Theme.success),
            mention_author=False,
        )

    @badges.command(name="revoke", description="[OWNER] Remove a badge from a player.")
    @is_owner()
    @commands.guild_only()
    async def badges_revoke(self, ctx: commands.Context, member: discord.Member, badge_key: str) -> None:
        spec = await self.bot.badges.revoke(ctx.guild.id, member.id, badge_key.lower())
        await ctx.reply(
            embed=Theme.embed("\U0001f3c5 Badge Revoked", f"Removed {spec.emoji} **{spec.name}** from {member.mention}.", Theme.info),
            mention_author=False,
        )

    @commands.hybrid_group(name="logchannel", invoke_without_command=True, description="[OWNER] Event logging channels.")
    @is_owner()
    @commands.guild_only()
    async def logchannel(self, ctx: commands.Context) -> None:
        await self.logchannel_list(ctx)

    @logchannel.command(name="set", description="[OWNER] Route an event category to a channel.")
    @is_owner()
    @commands.guild_only()
    async def logchannel_set(
        self, ctx: commands.Context, category: str, channel: discord.TextChannel
    ) -> None:
        category = category.lower()
        if category not in VALID_CATEGORIES:
            valid = ", ".join(f"`{c}`" for c in VALID_CATEGORIES)
            await ctx.reply(embed=Theme.error_embed(f"Unknown category. Valid: {valid}"), mention_author=False)
            return
        await self.bot.sink.set_channel(ctx.guild.id, category, channel.id)
        await ctx.reply(
            embed=Theme.embed(
                "\U0001f4dd Logging Configured",
                f"Events of type **{category}** now go to {channel.mention}.",
                Theme.success,
            ),
            mention_author=False,
        )

    @logchannel.command(name="remove", description="[OWNER] Stop logging an event category.")
    @is_owner()
    @commands.guild_only()
    async def logchannel_remove(self, ctx: commands.Context, category: str) -> None:
        removed = await self.bot.sink.remove_channel(ctx.guild.id, category.lower())
        verb = "removed" if removed else "was not configured"
        await ctx.reply(embed=Theme.embed("\U0001f4dd Logging Updated", f"Category `{category}` {verb}."), mention_author=False)

    @logchannel.command(name="list", description="[OWNER] Show configured logging channels.")
    @is_owner()
    @commands.guild_only()
    async def logchannel_list(self, ctx: commands.Context) -> None:
        routes = await self.bot.sink.list_channels(ctx.guild.id)
        if not routes:
            await ctx.reply(embed=Theme.embed("\U0001f4dd Logging", "No channels configured yet."), mention_author=False)
            return
        lines = [f"`{cat}` \u2192 <#{cid}>" for cat, cid in sorted(routes.items())]
        await ctx.reply(embed=Theme.embed("\U0001f4dd Logging Routes", "\n".join(lines)), mention_author=False)

    @commands.hybrid_command(name="reload_settings", description="[OWNER] Reload config/game.json without restart.")
    @is_owner()
    async def reload_settings(self, ctx: commands.Context) -> None:
        self.bot.reload_settings()
        await ctx.reply(embed=Theme.embed("\u267b\ufe0f Settings Reloaded", "game.json values are live.", Theme.success), mention_author=False)

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
        await ctx.reply(
            embed=Theme.embed(
                "\U0001f4ca Bot Statistics",
                (
                    f"Servers: **{len(self.bot.guilds)}**\n"
                    f"Players: **{players}**\n"
                    f"Total pulls: **{pulls:,}**\n"
                    f"Coins in circulation: **{coins:,}**\n"
                    f"Equipment pieces: **{equipment:,}**\n"
                    f"Active huntbots: **{bots}**\n"
                    f"Uptime: **{uptime // 3600:.0f}h {uptime % 3600 // 60:.0f}m**\n"
                    f"DB: **{CONFIG.database_url.split('://')[0]}** \u00b7 "
                    f"Python: **{platform.python_version()}** \u00b7 discord.py **{discord.__version__}**"
                ),
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(AdminCog(bot))
