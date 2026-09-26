"""Hunt commands: manual hunts and player profile."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.config import SETTINGS
from bot.services.hunt import HuntResult
from bot.ui import components as ui
from bot.ui.encounters import EncounterView
from bot.ui.theme import Theme


class HuntCog(GameMixin):
    """\U0001f3af Hunt & profile commands."""

    CATEGORY_EMOJI = "\U0001f3af"
    CATEGORY_LABEL = "Hunts & Profile"

    @commands.hybrid_command(name="hunt", description="Head out on a hunt for coins, XP and loot.")
    async def hunt(self, ctx: commands.Context) -> None:
        player, profile = await self.player_profile(ctx)
        result: HuntResult = await self.bot.hunt.hunt(self.scope_guild(ctx), player, profile)
        if result.encounter is not None:
            await ctx.reply(
                view=EncounterView(self.bot, player.guild_id, ctx.author.id, result.encounter),
                mention_author=False,
            )
            return
        view = ui.hunt_view(result, SETTINGS.currency.emoji)
        if result.success:
            await self.bot.records.submit_max(
                self.scope_guild(ctx), "hunt_best", "Largest single hunt",
                ctx.author.id, result.coins,
            )
        await self.bot.guild_prog.record_hunt(
            self.scope_guild(ctx), ctx.author.id, coins=result.coins if result.success else 0,
        )

        # extra lines that don't fit the compact card
        extras: list[str] = []
        for item in result.drops:
            pct = self.bot.content.equipment_roll_percentile(item)
            flair = f" \U0001f525 **GOD ROLL ({pct * 100:.0f}%)**" if pct is not None and pct >= 0.99 else ""
            extras.append(f"\U0001f081 Loot drop: {item}{flair}")
            if pct is not None and pct >= 0.99:
                await self.bot.badges.grant(self.scope_guild(ctx), ctx.author.id, "god_roller")
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
        owned_rows = await self.bot.db.fetch_all(
            "SELECT item_key FROM inventory WHERE guild_id = :g AND user_id = :u",
            {"g": player.guild_id, "u": target.id},
        )
        owned = frozenset(r["item_key"] for r in owned_rows)
        set_lines = [
            f"{s.emoji} **{s.name}** {have}/{total}"
            + (f" \u2014 {' \u00b7 '.join(t.title for t in tiers if t.title)}" if tiers else "")
            for s, have, total, tiers in self.bot.content.set_progress(owned)
            if have > 0
        ]
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
                set_lines=set_lines or None,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="records", aliases=["hallofrecords", "hof"], description="This server's hall of records.")
    async def records(self, ctx: commands.Context) -> None:
        rows = await self.bot.records.all_records(self.scope_guild(ctx) or 0)
        if not rows:
            await ctx.reply(
                embed=Theme.embed("\U0001f3c5 Hall of Records", "*No history written yet — go make some.*"),
                mention_author=False,
            )
            return
        lines = []
        for row in rows:
            try:
                date = row["set_at"][:10]
            except (TypeError, ValueError):
                date = "?"
            lines.append(
                f"\U0001f3c5 **{row['label']}** — <@{row['user_id']}> \u00b7 "
                f"**{row['value']:,}** \u00b7 {date}"
            )
        await ctx.reply(
            embed=Theme.embed("\U0001f3c5 Hall of Records", "\n".join(lines)),
            mention_author=False,
        )

    @commands.hybrid_command(name="hunt_info", description="How hunting works.")
    async def hunt_info(self, ctx: commands.Context) -> None:
        modifier = self.bot.hunt.todays_modifier()
        modifier_line = ""
        if modifier is not None:
            modifier_line = (
                f"\n\u2022 Today's modifier: **{modifier.emoji} {modifier.name}** "
                f"\u2014 {modifier.description}\n"
            )
        await ctx.reply(
            embed=Theme.embed(
                "\U0001f3af Hunting Guide",
                (
                    f"\u2022 `{ctx.clean_prefix}hunt` \u2014 fight a random enemy.\n"
                    f"\u2022 Stronger **equipment** and **upgrades** raise your power & luck.\n"
                    f"\u2022 Luck tilts enemy and loot rarity in your favour.\n"
                    f"\u2022 Card **sets** add small XP/coin/luck bonuses.\n"
                    f"\u2022 Base cooldown: **{SETTINGS.cooldown_seconds('hunt'):.0f}s** "
                    f"(Swiftness upgrade reduces it).\n"
                    f"\u2022 XP levels you up; every level gives +1% coins."
                    f"{modifier_line}"
                ),
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(HuntCog(bot))
