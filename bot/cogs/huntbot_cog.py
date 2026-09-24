"""Huntbot commands: buy, upgrade, toggle, collect, info."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import CONFIG
from bot.utils.embeds import COLOUR_GOLD, base_embed, error_embed, money


class HuntBotCog(GameMixin):
    """\U0001f916 Automate your hunting with a personal bot."""

    @commands.hybrid_group(name="huntbot", aliases=["hb"], invoke_without_command=True)
    async def huntbot(self, ctx: commands.Context) -> None:
        state = await self.bot.huntbot.get_state(ctx.author.id)
        if state is None:
            await ctx.reply(
                embed=error_embed("You don't own a huntbot yet. Buy one with `!huntbot buy`."),
                mention_author=False,
            )
            return
        await self.info(ctx)

    @huntbot.command(name="buy", description="Purchase your personal huntbot.")
    async def buy(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        cost = await self.bot.huntbot.buy(player)
        embed = base_embed(
            "\U0001f916 Huntbot Acquired!",
            (
                f"Paid {money(cost)}.\n\n"
                f"Your bot hunts **automatically** while active, banking rewards "
                f"until its battery fills. Collect with `{ctx.clean_prefix}huntbot collect`!"
            ),
            COLOUR_GOLD,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @huntbot.command(name="upgrade", description="Increase huntbot level (+income).")
    async def upgrade(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        new_level, cost = await self.bot.huntbot.upgrade(player)
        embed = base_embed(
            "\U0001f6e0\ufe0f Huntbot Upgraded",
            f"Level **{new_level}** for {money(cost)}.\nIncome per cycle: **{35 + new_level * 15:,}+** coins",
            COLOUR_GOLD,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @huntbot.command(name="toggle", description="Start or stop your huntbot.")
    async def toggle(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx.author)
        state = await self.bot.huntbot.require_state(player.user_id)
        await self.bot.huntbot.set_active(player, not state.active)
        verb = "started" if not state.active else "stopped"
        embed = base_embed(
            f"\U0001f7e2 Huntbot {verb.capitalize()}" if not state.active else f"\U0001f534 Huntbot {verb.capitalize()}",
            "It will now hunt automatically." if not state.active else "It will idle until you restart it.",
        )
        await ctx.reply(embed=embed, mention_author=False)

    @huntbot.command(name="collect", aliases=["claim"], description="Collect banked huntbot rewards.")
    async def collect(self, ctx: commands.Context) -> None:
        player, profile = await self.player_profile(ctx.author)
        harvest = player.upgrades.get("harvest", 0) * 0.04
        coins, items = await self.bot.huntbot.collect(player, harvest_bonus=harvest)
        lines = [f"Collected {money(coins)}."]
        if harvest:
            lines.append(f"Harvest upgrade bonus: +{harvest * 100:.0f}%")
        lines.extend(f"\U0001f081 {item}" for item in items)
        await ctx.reply(embed=base_embed("\U0001f4e6 Huntbot Collection", "\n".join(lines), COLOUR_GOLD), mention_author=False)

    @huntbot.command(name="info", description="Huntbot status & statistics.")
    async def info(self, ctx: commands.Context) -> None:
        state = await self.bot.huntbot.require_state(ctx.author.id)
        next_price = self.bot.huntbot.price(state.level)
        status = "\U0001f7e2 **Active**" if state.active else "\U0001f534 **Idle**"
        embed = base_embed(
            f"\U0001f916 Huntbot \u2014 Level {state.level}",
            (
                f"Status: {status}\n"
                f"Battery: **{state.battery}**/{CONFIG.huntbot_battery_capacity} "
                f"({'full \u2014 collect!' if state.battery >= CONFIG.huntbot_battery_capacity else 'charging'})\n"
                f"Income/cycle: **~{state.income_per_tick:,}** coins\n"
                f"Banked: **{state.unclaimed_coins:,}** coins"
                + (f" + {len(state.unclaimed_items)} item(s)" if state.unclaimed_items else "")
                + f"\nTotal hunts: **{state.hunts_done:,}**\n"
                f"Next upgrade: {money(next_price)}"
            ),
            COLOUR_GOLD,
        )
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot) -> None:
    await bot.add_cog(HuntBotCog(bot))
