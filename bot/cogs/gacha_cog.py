"""Gacha commands: pull, collection, shard exchange."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import CONFIG, CONSTANTS
from bot.models.game_data import load_game_data
from bot.services.gacha import PullSession
from bot.utils.embeds import COLOUR_GOLD, base_embed, error_embed, money

load_game_data()


class GachaCog(GameMixin):
    """\U0001f3a3 Gacha pulling & collection commands."""

    @commands.hybrid_command(name="pull", aliases=["gacha", "wish"], description="Pull a card or equipment.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    async def pull(self, ctx: commands.Context, count: int = 1) -> None:
        if count not in (1, 10):
            await ctx.reply(
                embed=error_embed("Use `1` for a single pull or `10` for a multi-pull (10% discount)."),
                mention_author=False,
            )
            return
        player, _ = await self.player_profile(ctx.author)
        session: PullSession = await self.bot.gacha.pull(player, count)

        lines = [o.describe() for o in session.outcomes]
        pity_note = " \U0001f6a8 **PITY!**" if any(o.pity_triggered for o in session.outcomes) else ""
        embed = base_embed(
            f"\U0001f3a3 Pull Results x{count}{pity_note}",
            "\n".join(lines),
            session.best.colour,
        )
        embed.add_field(
            name="Summary",
            value=(
                f"Cost: {money(session.cost)}\n"
                f"New cards: **{session.new_cards}**\n"
                f"Shards: +{session.shards_gained} {CONSTANTS.shard_emoji}\n"
                f"Pity: {player.pity_counter}/{CONFIG.gacha_pity_limit}"
            ),
            inline=False,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="collection", aliases=["coll", "dex"], description="View your card collection.")
    async def collection(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        owned, total = await self.bot.gacha.collection_progress(target.id)
        cards = await self.bot.gacha.collection(target.id)

        lines = [f"{c.rarity.emoji} **{c.name}** {c.rarity.stars} x{qty}" for c, qty in cards[:25]]
        missing = total - owned
        body = "\n".join(lines) if lines else "*Nothing yet — go pull!*"
        embed = base_embed(
            f"\U0001f4d6 {target.display_name}'s Collection",
            f"**{owned}**/{total} unique cards ({missing} missing)\n\n{body}",
            COLOUR_GOLD,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="sell_dupes", description="Convert duplicate cards into coins.")
    async def sell_dupes(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        gained = await self.bot.gacha.sell_duplicates(player)
        if gained == 0:
            await ctx.reply(embed=error_embed("No duplicate cards to sell."), mention_author=False)
            return
        embed = base_embed(
            "\U0001f4b1 Duplicates Sold",
            f"You converted spare cards into {money(gained)}.\nNew balance: {money(player.balance)}",
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_group(name="shards", invoke_without_command=True, description="Shard currency info.")
    async def shards(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        pull_cost = int(CONFIG.gacha_pull_cost / 10)
        embed = base_embed(
            f"{CONSTANTS.shard_emoji} Shards",
            (
                f"You have **{player.shards:,}** shards.\n\n"
                f"Shards come from duplicate pulls.\n"
                f"Spend them with `{ctx.clean_prefix}shards pull` \u2014 "
                f"one pull costs **{pull_cost}** shards."
            ),
            COLOUR_GOLD,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @shards.command(name="pull", description="Spend shards on a pull.")
    async def shards_pull(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        session = await self.bot.gacha.pull(player, count=1, use_shards=True)
        outcome = session.outcomes[0]
        pity_note = " \U0001f6a8 **PITY!**" if outcome.pity_triggered else ""
        embed = base_embed(
            f"{CONSTANTS.shard_emoji} Shard Pull{pity_note}",
            outcome.describe(),
            outcome.rarity.colour,
        )
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot) -> None:
    await bot.add_cog(GachaCog(bot))
