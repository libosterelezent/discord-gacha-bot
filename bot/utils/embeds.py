"""Embed/presentation helpers shared by all cogs."""
from __future__ import annotations

from typing import Iterable

import discord

from bot.config import CONSTANTS
from bot.models.player import Player, StatProfile

COLOUR_OK = discord.Colour.green()
COLOUR_BAD = discord.Colour.red()
COLOUR_INFO = discord.Colour.blurple()
COLOUR_GOLD = discord.Colour.gold()


def money(amount: int) -> str:
    return f"{CONSTANTS.currency_emoji} **{amount:,}** {CONSTANTS.currency_name}"


def base_embed(title: str, description: str | None = None, colour: discord.Colour = COLOUR_INFO) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, colour=colour)
    embed.set_footer(text="Gacha Bot • \u00a1buena suerte!")
    return embed


def error_embed(message: str) -> discord.Embed:
    return base_embed("\u26a0\ufe0f Error", message, COLOUR_BAD)


def player_embed(player: Player, profile: StatProfile, shards: int = 0) -> discord.Embed:
    xp_now, xp_next = player.xp_progress()
    embed = base_embed(
        f"\U0001f4dd Profile \u2014 Level {player.level}",
        "\n".join(profile.summary_lines()),
        COLOUR_GOLD,
    )
    embed.add_field(name="Balance", value=money(player.balance), inline=True)
    embed.add_field(name="Shards", value=f"{CONSTANTS.shard_emoji} {shards:,}", inline=True)
    embed.add_field(name="Power", value=f"\U0001f4aa {profile.power:,}", inline=True)
    embed.add_field(name="XP", value=f"{xp_now:,} / {xp_next:,}", inline=True)
    embed.add_field(name="Pulls", value=f"{player.total_pulls:,}", inline=True)
    embed.add_field(name="Pity", value=f"{player.pity_counter} / 90", inline=True)
    return embed


def progress_bar(current: int, maximum: int, width: int = 12) -> str:
    filled = round(width * min(current / max(maximum, 1), 1.0))
    return "\u2588" * filled + "\u2591" * (width - filled)


def format_lines(lines: Iterable[str], start: int = 1) -> str:
    return "\n".join(f"`{i}.` {line}" for i, line in enumerate(lines, start))
