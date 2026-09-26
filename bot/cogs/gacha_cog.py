"""Gacha commands: pull, collection, shard exchange."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import SETTINGS
from bot.ui import components as ui
from bot.ui.theme import Theme


class GachaCog(GameMixin):
    """\U0001f3a3 Gacha pulling & collection commands."""

    CATEGORY_EMOJI = "\U0001f3b2"
    CATEGORY_LABEL = "Gacha"

    @commands.hybrid_command(name="pull", aliases=["gacha", "wish"], description="Pull a card or equipment.")
    async def pull(self, ctx: commands.Context, count: int = 1) -> None:
        if count not in (1, SETTINGS.gacha.multi_count):
            await ctx.reply(
                embed=Theme.error_embed(
                    f"Use `1` for a single pull or `{SETTINGS.gacha.multi_count}` for a multi-pull (10% discount)."
                ),
                mention_author=False,
            )
            return
        player = await self.player(ctx)
        session = await self.bot.gacha.pull(self.scope_guild(ctx), player, count)
        for outcome in session.outcomes:
            if outcome.roll_pct is not None and outcome.roll_pct >= 0.99:
                await self.bot.badges.grant(self.scope_guild(ctx), ctx.author.id, "god_roller")
            if outcome.rarity.tier >= 6:
                await self.bot.records.claim_first(
                    self.scope_guild(ctx), "first_mythic", "First Mythic pull",
                    ctx.author.id, player.total_pulls,
                )
        await ctx.reply(
            view=ui.pull_view(session, SETTINGS.shards.emoji, SETTINGS.gacha.pity_limit, puller=ctx.author.display_name),
            mention_author=False,
        )

    @commands.hybrid_command(name="collection", aliases=["coll", "dex"], description="View your card collection.")
    async def collection(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        player = await self.player(ctx, target)
        owned, total = await self.bot.gacha.collection_progress(player.guild_id, target.id)
        cards = await self.bot.gacha.collection(player.guild_id, target.id)
        lines = [f"{c.rarity.emoji} **{c.name}** {c.rarity.stars} x{qty}" for c, qty in cards[:25]]
        owned_keys = frozenset(c.key for c, _ in cards)
        set_lines = [
            f"{s.emoji} **{s.name}** \u2014 {have}/{total_cards}"
            + (f" \u2713 {' \u00b7 '.join(t.title for t in tiers if t.title)}" if tiers else "")
            for s, have, total_cards, tiers in self.bot.content.set_progress(owned_keys)
        ]
        await ctx.reply(
            view=ui.collection_view(target.display_name, lines, owned, total, set_lines=set_lines),
            mention_author=False,
        )

    @commands.hybrid_command(name="sell_dupes", description="Convert duplicate cards into coins.")
    async def sell_dupes(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        gained = await self.bot.gacha.sell_duplicates(self.scope_guild(ctx), player)
        if gained == 0:
            await ctx.reply(embed=Theme.error_embed("No duplicate cards to sell."), mention_author=False)
            return
        await ctx.reply(
            ui.plain(f"\U0001f4b1 Sold duplicates for {SETTINGS.money(gained)} — new balance: {SETTINGS.money(player.balance)}."),
            mention_author=False,
        )

    @commands.hybrid_group(name="shards", invoke_without_command=True, description="Shard currency info.")
    async def shards(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        pull_cost = int(SETTINGS.gacha.pull_cost / SETTINGS.gacha.shard_pull_divisor)
        explanation = (
            f"Shards come from duplicate pulls.\n"
            f"Spend them with `{ctx.clean_prefix}shards pull` — one pull costs **{pull_cost}** shards."
        )
        await ctx.reply(view=ui.shard_info_view(player.shards, explanation), mention_author=False)

    @shards.command(name="pull", description="Spend shards on a pull.")
    async def shards_pull(self, ctx: commands.Context) -> None:
        player = await self.player(ctx)
        session = await self.bot.gacha.pull(self.scope_guild(ctx), player, 1, use_shards=True)
        await ctx.reply(
            view=ui.pull_view(session, SETTINGS.shards.emoji, SETTINGS.gacha.pity_limit, puller=ctx.author.display_name),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(GachaCog(bot))
