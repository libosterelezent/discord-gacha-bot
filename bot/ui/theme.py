"""Central presentation theme.

Every colour, footer and title pattern in the bot lives here — cogs and
views must not scatter random strings or ad-hoc colours. Adjust the
brand once, it adjusts everywhere.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot.content.registry import RarityTier


class Theme:
    """Design tokens for the whole bot."""

    bot_name: str = "Gacha Hunter"
    tagline: str = "pull \u00b7 forge \u00b7 hunt \u00b7 prosper"

    # -- palette -------------------------------------------------------------
    primary: int = 0xFFB300      # gold — default accent for rich views
    success: int = 0x57F287      # green
    error: int = 0xED4245        # red
    info: int = 0x5865F2         # blurple
    neutral: int = 0x2B2D31      # quiet grey

    # -- attribution -----------------------------------------------------------
    @classmethod
    def footer(cls) -> str:
        return cls.bot_name

    @classmethod
    def footer_with_tagline(cls) -> str:
        return f"{cls.bot_name} \u2022 {cls.tagline}"

    # -- embed factories ---------------------------------------------------------
    @classmethod
    def embed(
        cls,
        title: str | None = None,
        description: str | None = None,
        colour: int | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=description,
            colour=colour if colour is not None else cls.info,
        )
        embed.set_footer(text=cls.footer())
        return embed

    @classmethod
    def error_embed(cls, message: str) -> discord.Embed:
        return cls.embed("\u26a0\ufe0f " + "Error", message, cls.error)

    @classmethod
    def rarity_colour(cls, rarity: "RarityTier") -> int:
        return rarity.colour

    # -- shared text helpers -------------------------------------------------------
    @classmethod
    def money(cls, amount: int, currency_emoji: str, currency_name: str) -> str:
        return f"{currency_emoji} **{amount:,}** {currency_name}"

    @staticmethod
    def progress_bar(current: int, maximum: int, width: int = 12) -> str:
        filled = round(width * min(current / max(maximum, 1), 1.0))
        return "\u2588" * filled + "\u2591" * (width - filled)
