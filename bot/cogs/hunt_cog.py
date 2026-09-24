"""Hunt commands: manual hunts and player profile."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import CONFIG
from bot.services.hunt import HuntResult
from bot.utils.embeds import base_embed, error_embed, money, player_embed


class HuntCog(GameMixin):
    """\U0001f3af Hunt & profile commands."""

    @commands.hybrid_command(name="hunt", description="Head out on a hunt for coins, XP and loot.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def hunt(self, ctx: commands.Context) -> None:
        player, profile = await self.player_profile(ctx.author)
        result: HuntResult = await self.bot.hunt.hunt(player, profile)

        lines = [result.headline]
        if result.success:
            lines.append(f"Reward: {money(result.coins)} \u00b7 +{result.xp} XP")
        else:
            lines.append(f"Scavenged {money(result.coins)} \u00b7 +{result.xp} XP")
        for item in result.drops:
            lines.append(f"\U0001f081 Loot drop: {item}")
        if result.card_key:
            from bot.models.items import ItemRegistry
            card = ItemRegistry.card(result.card_key)
            if card:
                lines.append(f"\U0001f5fd Card drop: **{card.name}** {card.rarity.emoji} {card.rarity.stars}")
        if result.level_up:
            lines.append(f"\U0001f53c **LEVEL UP!** You are now level **{result.level_up}**.")

        await ctx.reply(
            embed=base_embed("The Hunt", "\n".join(lines), result.enemy.rarity.colour),
            mention_author=False,
        )

    @commands.hybrid_command(name="profile", aliases=["me", "stats"], description="View your hunter profile.")
    async def profile(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        player, profile = await self.bot.player_profile(target.id)
        shards = await self.bot.db.fetch_val(
            "SELECT shards FROM players WHERE user_id = ?", (str(target.id),)
        )
        embed = player_embed(player, profile, shards=int(shards or 0))
        embed.title = f"\U0001f4dd {target.display_name}'s Profile \u2014 Level {player.level}"
        hb = await self.bot.huntbot.get_state(target.id)
        if hb:
            embed.add_field(
                name="\U0001f916 Huntbot",
                value=(
                    f"Lv.{hb.level} \u00b7 {'\U0001f7e2 Active' if hb.active else '\U0001f534 Idle'}\n"
                    f"Battery: {hb.battery}/{CONFIG.huntbot_battery_capacity}"
                ),
                inline=False,
            )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="hunt_info", description="How hunting works.")
    async def hunt_info(self, ctx: commands.Context) -> None:
        embed = base_embed(
            "\U0001f3af Hunting Guide",
            (
                f"\u2022 `{ctx.clean_prefix}hunt` \u2014 fight a random enemy.\n"
                f"\u2022 Stronger **equipment** and **upgrades** raise your power & luck.\n"
                f"\u2022 Luck tilts enemy and loot rarity in your favour.\n"
                f"\u2022 Base cooldown: **{CONFIG.hunt_cooldown_seconds}s** "
                f"(Swiftness upgrade reduces it).\n"
                f"\u2022 XP levels you up; every level gives +1% coins."
            ),
        )
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot) -> None:
    await bot.add_cog(HuntCog(bot))
