"""Huntbot commands: buy, upgrade, toggle, collect, info."""
from __future__ import annotations

from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import SETTINGS
from bot.ui import components as ui
from bot.ui.theme import Theme


class HuntBotCog(GameMixin):
    """\U0001f916 Automate your hunting with a personal bot."""

    CATEGORY_EMOJI = "\U0001f916"
    CATEGORY_LABEL = "Huntbot"

    @commands.hybrid_group(name="huntbot", aliases=["hb"], invoke_without_command=True)
    async def huntbot(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx)
        state = await self.bot.huntbot.get_state(player.guild_id, ctx.author.id)
        if state is None:
            await ctx.reply(
                embed=Theme.error_embed(f"You don't own a huntbot yet. Buy one with `{ctx.clean_prefix}huntbot buy`."),
                mention_author=False,
            )
            return
        await self.info(ctx)

    @huntbot.command(name="buy", description="Purchase your personal huntbot.")
    async def buy(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx)
        cost = await self.bot.huntbot.buy(self.scope_guild(ctx), player)
        await ctx.reply(
            embed=Theme.embed(
                "\U0001f916 Huntbot Acquired!",
                (
                    f"You paid {SETTINGS.money(cost)}.\n\n"
                    f"Your bot hunts **automatically** while active, banking rewards "
                    f"until its battery fills. Collect with `{ctx.clean_prefix}huntbot collect`!"
                ),
                Theme.success,
            ),
            mention_author=False,
        )

    @huntbot.command(name="upgrade", description="Increase huntbot level (+income).")
    async def upgrade(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx)
        new_level, cost = await self.bot.huntbot.upgrade(self.scope_guild(ctx), player)
        income = self.bot.huntbot.income_per_tick(new_level)
        await ctx.reply(
            embed=Theme.embed(
                "\U0001f6e0\ufe0f Huntbot Upgraded",
                f"Level **{new_level}** for {SETTINGS.money(cost)}.\nIncome per cycle: **{income:,}+** coins",
                Theme.primary,
            ),
            mention_author=False,
        )

    @huntbot.command(name="toggle", description="Start or stop your huntbot.")
    async def toggle(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx)
        state = await self.bot.huntbot.require_state(player.guild_id, player.user_id)
        await self.bot.huntbot.set_active(self.scope_guild(ctx), player, not state.active)
        if not state.active:
            await ctx.reply(ui.plain("\U0001f7e2 Huntbot started — it will now hunt automatically."), mention_author=False)
        else:
            await ctx.reply(ui.plain("\U0001f534 Huntbot stopped — it will idle until you restart it."), mention_author=False)

    @huntbot.command(name="collect", aliases=["claim"], description="Collect banked huntbot rewards.")
    async def collect(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx)
        harvest_spec = self.bot.content.upgrade("harvest")
        harvest = player.upgrades.get("harvest", 0) * (harvest_spec.effect_per_level if harvest_spec else 0.0)
        coins, items = await self.bot.huntbot.collect(self.scope_guild(ctx), player, harvest_bonus=harvest)
        lines = [f"Collected {SETTINGS.money(coins)}."]
        if harvest:
            lines.append(f"Harvest upgrade bonus: +{harvest * 100:.0f}%")
        lines.extend(f"\U0001f081 {item}" for item in items)
        await ctx.reply(view=ui.card_view("## \U0001f4e6 Huntbot Collection", "\n".join(lines), Theme.success), mention_author=False)

    @huntbot.command(name="info", description="Huntbot status & statistics.")
    async def info(self, ctx: commands.Context) -> None:
        player, _ = await self.player_profile(ctx)
        state = await self.bot.huntbot.require_state(player.guild_id, ctx.author.id)
        next_price = self.bot.huntbot.price(state.level)
        income = self.bot.huntbot.income_per_tick(state.level)
        await ctx.reply(
            view=ui.huntbot_view(
                state,
                income,
                SETTINGS.money(next_price),
                SETTINGS.huntbot.battery_capacity,
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(HuntBotCog(bot))
