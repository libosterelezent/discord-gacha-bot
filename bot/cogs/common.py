"""Shared cog plumbing: a mixin giving cogs typed access to services and
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

    def __init__(self, bot: GachaBot) -> None:
        self.bot = bot

    async def player_profile(self, user: discord.abc.User) -> tuple[Player, StatProfile]:
        return await self.bot.player_profile(user.id)
