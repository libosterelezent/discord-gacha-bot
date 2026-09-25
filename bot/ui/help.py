"""Interactive Components V2 help menu.

Categories are **generated from the loaded cogs**, not hardcoded — when
a cog gains commands the help menu updates itself. Navigation: a
dropdown to jump to any category, plus Home / previous / next buttons.
"""
from __future__ import annotations

from typing import Iterable

import discord
from discord import ui
from discord.ext import commands

from bot.ui.theme import Theme

# display order for known categories; unknown cogs are appended after
_CATEGORY_ORDER: tuple[str, ...] = (
    "Economy", "Gacha", "Hunts & Profile", "Huntbot", "Equipment", "Upgrades", "Admin",
)


def _cog_meta(cog: commands.Cog) -> tuple[str, str, list[str]]:
    """Return (emoji, label, command lines) for a cog."""
    emoji: str = getattr(cog, "CATEGORY_EMOJI", "\U0001f5c2\ufe0f")
    label: str = getattr(cog, "CATEGORY_LABEL", cog.qualified_name.title())
    lines: list[str] = []
    for command in cog.get_commands():
        if isinstance(command, commands.HybridCommand):
            _append_command(lines, command.qualified_name, command.aliases, command.description or command.help or "")
        elif isinstance(command, commands.HybridGroup):
            _append_command(lines, command.qualified_name, command.aliases, command.description or "")
            for sub in command.commands:
                if isinstance(sub, commands.HybridCommand):
                    _append_command(
                        lines, sub.qualified_name, sub.aliases, sub.description or sub.help or ""
                    )
    return emoji, label, lines


def _append_command(lines: list[str], name: str, aliases: Iterable[str], description: str) -> None:
    alias = [a for a in aliases if " " not in a]
    alias_part = f" `{'` `'.join(alias)}`" if alias else ""
    description = (description or "").strip().split("\n")[0]
    lines.append(f"`{name}`{alias_part} \u2014 {description}" if description else f"`{name}`{alias_part}")


def build_categories(bot: commands.Bot) -> dict[str, tuple[str, str, list[str]]]:
    """Collect help categories from every loaded cog."""
    categories: dict[str, tuple[str, str, list[str]]] = {}
    for cog in bot.cogs.values():
        emoji, label, lines = _cog_meta(cog)
        if not lines:
            continue
        categories[label] = (emoji, label, lines)

    def sort_key(item: tuple[str, ...]) -> tuple[int, str]:
        label = item[0]
        return (_CATEGORY_ORDER.index(label) if label in _CATEGORY_ORDER else len(_CATEGORY_ORDER), label)

    return dict(sorted(categories.items(), key=sort_key))


class CategorySelect(ui.Select):
    def __init__(self, categories: dict[str, tuple[str, str, list[str]]], current: str) -> None:
        options = [
            discord.SelectOption(
                label=f"{meta[1]} commands",
                emoji=meta[0],
                value=key,
                description=f"{len(meta[2])} commands",
                default=(key == current),
            )
            for key, meta in categories.items()
        ]
        super().__init__(placeholder="Choose a category\u2026", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HelpMenuView = self.view  # type: ignore[assignment]
        await interaction.response.edit_message(
            view=HelpMenuView(view.bot, view.prefix, self.values[0])
        )


class NavButton(ui.Button):
    def __init__(self, label: str, emoji: str, target: str, row: int) -> None:
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=row)
        self.target = target

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HelpMenuView = self.view  # type: ignore[assignment]
        await interaction.response.edit_message(
            view=HelpMenuView(view.bot, view.prefix, self.target)
        )


class HelpMenuView(ui.LayoutView):
    """Components V2 layout for the help menu (rebuilt per category)."""

    def __init__(self, bot: commands.Bot, prefix: str, category: str = "home") -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.prefix = prefix
        categories = build_categories(bot)
        self.categories = categories

        container = ui.Container(accent_colour=Theme.primary)

        if category == "home":
            total = sum(len(meta[2]) for meta in categories.values())
            header = (
                f"## \U0001f3b0 {Theme.bot_name} \u2014 Help\n"
                f"Pull cards, forge gear, hunt monsters, grow your hoard.\n"
                f"-# {total} commands across {len(categories)} categories \u00b7 "
                f"`{prefix}<command>` or `/<command>`"
            )
            body = "\n".join(
                f"{meta[0]} **{meta[1]}** \u2014 {len(meta[2])} commands"
                for meta in categories.values()
            )
        else:
            emoji, label, lines = categories[category]
            header = f"## {emoji} {label} Commands\n-# `{prefix}<command>` or `/<command>`"
            body = "\n".join(f"\u2022 {ln}" for ln in lines)

        avatar_url = bot.user.display_avatar.url if bot.user else None
        if avatar_url:
            container.add_item(
                ui.Section(header, accessory=ui.Thumbnail(media=avatar_url, description=Theme.footer()))
            )
        else:
            container.add_item(ui.TextDisplay(header))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(body))
        container.add_item(ui.Separator())

        container.add_item(ui.ActionRow(CategorySelect(categories, category)))

        keys = list(categories)
        if keys:
            if category == "home":
                prev_key, next_key = keys[-1], keys[0]
            else:
                idx = keys.index(category)
                prev_key, next_key = keys[idx - 1], keys[(idx + 1) % len(keys)]
            row = ui.ActionRow(
                NavButton("Home", "\U0001f3e0", "home", 0),
                NavButton("Prev", "\u2b05\ufe0f", prev_key, 0),
                NavButton("Next", "\u27a1\ufe0f", next_key, 0),
            )
            container.add_item(row)

        container.add_item(
            ui.TextDisplay(f"-# {Theme.bot_name} \u2022 {Theme.tagline}")
        )
        self.add_item(container)
