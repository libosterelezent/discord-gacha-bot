"""Shared cog plumbing: typed access to services, scope resolution and
a one-call way to fetch (player, profile) for the invoking user.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.core.gacha_bot import GachaBot
from bot.models.player import Player, StatProfile


class GameMixin(commands.Cog):
    """Mixin base for game cogs (co-operative multiple inheritance)."""

    bot: GachaBot

    # help-menu metadata (read by bot.ui.help)
    CATEGORY_EMOJI: str = "\U0001f5c2\ufe0f"
    CATEGORY_LABEL: str = ""

    def __init__(self, bot: GachaBot) -> None:
        self.bot = bot

    @staticmethod
    def scope_guild(ctx: commands.Context) -> int | None:
        """The guild used for scoping game data (None in DMs)."""
        return ctx.guild.id if ctx.guild else None

    async def player_profile(
        self, ctx: commands.Context, user: discord.abc.User | None = None
    ) -> tuple[Player, StatProfile]:
        target = user or ctx.author
        return await self.bot.player_profile(self.scope_guild(ctx), target.id)
