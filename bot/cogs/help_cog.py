"""Interactive Components V2 help menu.

Uses Discord's Components V2 (LayoutView + Container + TextDisplay +
Section/Thumbnail + Separator + ActionRow) with a string-select to
switch command categories in-place. Replaces the DefaultHelpCommand.
"""
from __future__ import annotations

import discord
from discord import ui
from discord.ext import commands

COLOUR_GOLD = 0xFFB300

# key: (emoji, label, [command lines])
_CATEGORIES: dict[str, tuple[str, str, list[str]]] = {
    "economy": (
        "\U0001fa99",
        "Economy",
        [
            "`balance` `bal` — show your coins & shards",
            "`daily` — claim your daily reward (24h)",
            "`work` — earn coins (hourly, Greed boost)",
            "`pay <@user> <amount>` — send coins to someone",
            "`gamble <amount>` — 50/50 double or nothing",
            "`leaderboard` `lb` — top 10 richest hunters",
        ],
    ),
    "gacha": (
        "\U0001f3b2",
        "Gacha",
        [
            "`pull [1|10]` — pull cards & gear (10-pull: -10%)",
            "`collection [@user]` — card dex & progress",
            "`sell_dupes` — convert spare cards into coins",
            "`shards` — shard info (from duplicate pulls)",
            "`shards pull` — spend 10 shards on a pull",
            "-# Pity: guaranteed Legendary/Mythic every 90 pulls; odds ramp after 75.",
        ],
    ),
    "hunt": (
        "\U0001f3af",
        "Hunts & Profile",
        [
            "`hunt` — fight a random enemy for loot (45s)",
            "`profile [@user]` `me` — stats, gear, pity, huntbot",
            "`hunt_info` — how hunting, luck & cooldowns work",
            "-# Power decides win rate; luck tilts enemy & loot rarity.",
        ],
    ),
    "huntbot": (
        "\U0001f916",
        "Huntbot",
        [
            "`huntbot` / `huntbot info` — status & banked rewards",
            "`huntbot buy` — purchase your bot (auto-activates)",
            "`huntbot upgrade` — +income & efficiency per level",
            "`huntbot toggle` — start / stop auto-hunting",
            "`huntbot collect` — sweep banked coins & items",
            "-# Battery caps at 24 cycles \u2014 collect regularly!",
        ],
    ),
    "equipment": (
        "\u2694\ufe0f",
        "Equipment",
        [
            "`equipment` `gear` — list your gear (📮 = equipped)",
            "`equip <id>` — equip a weapon/armor/amulet",
            "`unequip <id>` — take a piece off",
            "`upgequip <id>` — forge +1 (compounding stats, max +10)",
            "`sellgear <id>` — sell a piece for coins",
        ],
    ),
    "upgrades": (
        "\U0001f527",
        "Upgrades",
        [
            "`upgrades` — workshop: levels, effects, next costs",
            "`upgrades buy <name>` — buy one level",
            "-# Perks: Fortune (luck) \u00b7 Greed (coins) \u00b7 Swiftness (cooldowns) \u00b7 Power \u00b7 Harvest (huntbot).",
        ],
    ),
    "admin": (
        "\U0001f6e1\ufe0f",
        "Admin",
        [
            "`grant <@user> <amount>` — mint coins (owner)",
            "`botstats` — players, pulls, circulation, uptime",
        ],
    ),
}


class CategorySelect(ui.Select):
    """String select listing every help category."""

    def __init__(self, current: str) -> None:
        options = [
            discord.SelectOption(
                label=f"{meta[1]} commands",
                emoji=meta[0],
                value=key,
                description=f"{len(meta[2])} commands",
                default=(key == current),
            )
            for key, meta in _CATEGORIES.items()
        ]
        super().__init__(placeholder="Choose a category…", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "HelpMenuView" = self.view  # type: ignore[assignment]
        await interaction.response.edit_message(
            view=HelpMenuView(view.bot, view.prefix, self.values[0])
        )


class HomeButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Home", emoji="\ud83c\udfe0", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "HelpMenuView" = self.view  # type: ignore[assignment]
        await interaction.response.edit_message(view=HelpMenuView(view.bot, view.prefix, "home"))


class HelpMenuView(ui.LayoutView):
    """Components V2 layout for the help menu (rebuilt per category)."""

    def __init__(self, bot: commands.Bot, prefix: str, category: str = "home") -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.prefix = prefix

        container = ui.Container(accent_colour=COLOUR_GOLD)

        if category == "home":
            total = sum(len(meta[2]) for meta in _CATEGORIES.values())
            header = (
                f"## \U0001f3b0 Gacha Hunter \u2014 Help\n"
                f"Pull cards, forge gear, hunt monsters, grow your hoard.\n"
                f"-# {total} commands across {len(_CATEGORIES)} categories \u00b7 "
                f"`{prefix}<command>` or `/<command>`"
            )
            body = "\n".join(
                f"{meta[0]} **{meta[1]}** \u2014 {len(meta[2])} commands"
                for meta in _CATEGORIES.values()
            )
        else:
            emoji, label, lines = _CATEGORIES[category]
            header = f"## {emoji} {label} Commands\n-# `{prefix}<command>` or `/<command>`"
            body = "\n".join(f"\u2022 {ln}" for ln in lines)

        avatar_url = bot.user.display_avatar.url if bot.user else None
        if avatar_url:
            header_section = ui.Section(
                header,
                accessory=ui.Thumbnail(media=avatar_url, description="Bot avatar"),
            )
            container.add_item(header_section)
        else:
            container.add_item(ui.TextDisplay(header))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(body))
        container.add_item(ui.Separator())

        nav_row = ui.ActionRow(CategorySelect(category))
        container.add_item(nav_row)

        if category != "home":
            container.add_item(ui.ActionRow(HomeButton()))

        container.add_item(
            ui.TextDisplay(f"-# \U0001fa99 Start with `{prefix}daily`, `{prefix}pull` and `{prefix}hunt`!")
        )
        self.add_item(container)


class HelpCog(commands.Cog):
    """\U0001f4d6 Interactive help menu (Components V2)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="help", description="Interactive help menu with all commands.")
    async def help(self, ctx: commands.Context) -> None:
        prefix = ctx.clean_prefix
        await ctx.reply(view=HelpMenuView(self.bot, prefix), mention_author=False)


async def setup(bot) -> None:
    await bot.add_cog(HelpCog(bot))
