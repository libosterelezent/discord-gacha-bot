"""Guild layer commands: server identity, relics and weekly expeditions."""
from __future__ import annotations

from discord.ext import commands

from bot.cogs.common import GameMixin
from bot.ui import components as ui
from bot.ui.theme import Theme


class GuildCog(GameMixin):
    """🏰 Server identity: reputation, relics, expeditions."""

    CATEGORY_EMOJI = "\U0001f3d8\ufe0f"
    CATEGORY_LABEL = "Guild"

    @commands.hybrid_command(name="guild", description="Your server's guild: reputation, relic and top hunters.")
    @commands.guild_only()
    async def guild(self, ctx: commands.Context) -> None:
        guild_id = self.scope_guild(ctx)
        assert guild_id is not None
        state = await self.bot.guild_prog.state(guild_id)
        level = self.bot.guild_prog.reputation_level(int(state["reputation"]))
        title = self.bot.guild_prog.level_title(level)
        relic = await self.bot.guild_prog.get_relic(guild_id)
        expedition = await self.bot.guild_prog.expedition_status(guild_id)
        contributors = await self.bot.guild_prog.contributors(guild_id, limit=5)

        next_level_rep = 100 * (level + 1) ** 2
        lines = [
            f"### \U0001f3d8\ufe0f {ctx.guild.name}",
            f"Reputation **{int(state['reputation']):,}** \u2014 Level **{level}** ({title})",
        ]
        if relic is not None:
            lines.append(f"Relic: {relic.emoji} **{relic.name}** \u2014 {relic.flavor}")
        else:
            lines.append(f"Relic: *none — choose one with `{ctx.clean_prefix}relic set <key>`*")
        if expedition.spec is not None:
            lines.append(
                f"\n### {expedition.spec.emoji} {expedition.spec.name} \u2014 "
                f"{int(expedition.pct * 100)}%"
                + (" \u2713 complete" if expedition.done else "")
            )
            lines.append(f"-# {expedition.progress:,}/{expedition.target:,} \u00b7 next level at {next_level_rep:,} rep")
        if contributors:
            names = " \u00b7 ".join(
                f"<@{c['user_id']}> ({c['hunts']}h/{c['pulls']}p)" for c in contributors
            )
            lines.append(f"\n**This week's hunters:** {names}")
        await ctx.reply(view=ui.card_view("## \U0001f3d8\ufe0f Guild", "\n".join(lines), accent=Theme.primary), mention_author=False)

    @commands.hybrid_group(name="relic", invoke_without_command=True, description="Server relics.")
    @commands.guild_only()
    async def relic(self, ctx: commands.Context) -> None:
        current = await self.bot.guild_prog.get_relic(self.scope_guild(ctx))
        lines = []
        for spec in self.bot.content.all_relics():
            active = " \u2713 **active**" if current is not None and current.key == spec.key else ""
            lines.append(f"{spec.emoji} **{spec.name}** (`{spec.key}`){active}\n\u2003{spec.flavor}")
        await ctx.reply(
            view=ui.card_view(
                "## \U0001fa9d Server Relic",
                "\n".join(lines) + f"\n\n-# one active per server; change with `{ctx.clean_prefix}relic set <key>` (weekly)",
                accent=Theme.primary,
            ),
            mention_author=False,
        )

    @relic.command(name="set", description="Choose this server's relic (changeable once per week).")
    @commands.guild_only()
    async def relic_set(self, ctx: commands.Context, key: str) -> None:
        spec = await self.bot.guild_prog.set_relic(self.scope_guild(ctx), key.strip().lower())
        await ctx.reply(
            embed=Theme.embed(
                f"{spec.emoji} {spec.name} activated",
                f"{spec.flavor}.\nThe forge cools for a week — the next change unlocks next week.",
                Theme.success,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="expedition", description="This week's guild expedition and standings.")
    @commands.guild_only()
    async def expedition(self, ctx: commands.Context) -> None:
        guild_id = self.scope_guild(ctx)
        assert guild_id is not None
        status = await self.bot.guild_prog.expedition_status(guild_id)
        if status.spec is None:
            await ctx.reply(embed=Theme.error_embed("No expedition is running."), mention_author=False)
            return
        bar = Theme.progress_bar(status.progress, status.target, width=14)
        hit = {m for m in status.milestones_hit}
        milestones = " ".join(
            ("\u25cf" if m in hit or status.done else "\u25cb") + f"{int(m * 100)}%"
            for m in self.bot.content.expedition_milestones
        )
        body = (
            f"{status.spec.description}\n\n"
            f"`{bar}` **{status.progress:,}**/{status.target:,} "
            f"({int(status.pct * 100)}%)\n"
            f"Milestones: {milestones}\n"
            + ("-# \u2713 expedition complete — contributors have been paid" if status.done else "")
        )
        standings = await self.bot.guild_prog.expedition_leaderboard(limit=5)
        if standings:
            rows = []
            for i, row in enumerate(standings, 1):
                row["guild_id"] = int(row["guild_id"])
                mark = " \u2713" if row["guild_id"] == guild_id else ""
                name = f"Guild `{row['guild_id']}`"
                rows.append(f"`{i}.` {name} \u2014 {int(row['expedition_progress']):,}{mark}")
            body += "\n\n### Cross-guild standings\n" + "\n".join(rows)
        await ctx.reply(
            view=ui.card_view(
                f"## {status.spec.emoji} {status.spec.name}",
                body, accent=Theme.primary, footer="progress feeds from normal play",
            ),
            mention_author=False,
        )


async def setup(bot) -> None:
    await bot.add_cog(GuildCog(bot))
