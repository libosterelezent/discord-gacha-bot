"""Hunt commands: manual hunts and player profile."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import SETTINGS
from bot.services.hunt import HuntResult
from bot.ui import components as ui
from bot.ui.theme import Theme


class HuntCog(GameMixin):
    """\U0001f3af Hunt & profile commands."""

    CATEGORY_EMOJI = "\U0001f3af"
    CATEGORY_LABEL = "Hunts & Profile"

    @commands.hybrid_command(name="hunt", description="Head out on a hunt for coins, XP and loot.")
    async def hunt(self, ctx: commands.Context) -> None:
        player, profile = await self.player_profile(ctx)
        result: HuntResult = await self.bot.hunt.hunt(self.scope_guild(ctx), player, profile)
        view = ui.hunt_view(result, SETTINGS.currency.emoji)

        # extra lines that don't fit the compact card
        extras: list[str] = []
        for item in result.drops:
            extras.append(f"\U0001f081 Loot drop: {item}")
        if result.card_key:
            card = self.bot.content.card(result.card_key)
            if card:
                extras.append(f"\U0001f5fd Card drop: **{card.name}** {card.rarity.emoji} {card.rarity.stars}")
        if result.level_up:
            extras.append(f"\U0001f53c **LEVEL UP!** You are now level **{result.level_up}**.")

        await ctx.reply(view=view, mention_author=False)
        if extras:
            await ctx.send("\n".join(extras))

    @commands.hybrid_command(name="profile", aliases=["me", "stats"], description="View your hunter profile.")
    async def profile(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        player, profile = await self.bot.player_profile(self.scope_guild(ctx), target.id)
        hb = await self.bot.huntbot.get_state(player.guild_id, target.id)
        badges = await self.bot.badges.badge_strings(target.id, self.scope_guild(ctx))
        await ctx.reply(
            view=ui.profile_view(
                player,
                profile,
                target.display_name,
                target.display_avatar.url,
                huntbot=hb,
                badges=badges,
                battery_capacity=SETTINGS.huntbot.battery_capacity,
                pity_limit=SETTINGS.gacha.pity_limit,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="hunt_info", description="How hunting works.")
    async def hunt_info(self, ctx: commands.Context) -> None:
        await ctx.reply(
            embed=Theme.embed(
                "\U0001f3af Hunting Guide",
                (
                    f"\u2022 `{ctx.clean_prefix}hunt` \u2014 fight a random enemy.\n"
                    f"\u2022 Stronger **equipment** and **upgrades** raise your power & luck.\n"
                    f"\u2022 Luck tilts enemy and loot rarity in your favour.\n"
                    f"\u2022 Base cooldown: **{SETTINGS.cooldown_seconds('hunt'):.0f}s** "
                    f"(Swiftness upgrade reduces it).\n"
                    f"\u2022 XP levels you up; every level gives +1% coins."
                ),
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(HuntCog(bot))
