"""Interactive rare-encounter views (Components V2).

The hunt command returns an encounter card with one button per choice;
only the hunter who triggered it may answer, and the outcome replaces
the card in place (UPDATE_MESSAGE).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.ui.theme import Theme

if TYPE_CHECKING:
    from bot.content.registry import EncounterSpec
    from bot.services.hunt import EncounterResolution


class EncounterChoiceButton(ui.Button["EncounterView"]):
    def __init__(self, choice_key: str, label: str, emoji: str) -> None:
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary, row=0)
        self.choice_key = choice_key

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert view is not None
        if interaction.user.id != view.user_id:
            await interaction.response.send_message(
                "This encounter isn't yours to answer.", ephemeral=True
            )
            return
        if view.resolved:
            await interaction.response.send_message(
                "Already resolved — the moment has passed.", ephemeral=True
            )
            return
        view.resolved = True
        player, profile = await view.bot.player_profile(view.guild_id, view.user_id)
        resolution: EncounterResolution = await view.bot.hunt.resolve_encounter(
            view.guild_id, player, profile, view.encounter, self.choice_key
        )
        outcome = view.resolution_lines(resolution)
        await interaction.response.edit_message(view=_outcome_view(view.encounter, outcome))
        view.stop()


class EncounterView(ui.LayoutView):
    """The choice card shown when a hunt triggers a rare encounter."""

    def __init__(self, bot, guild_id: int, user_id: int, encounter: "EncounterSpec") -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.encounter = encounter
        self.resolved = False

        container = ui.Container(accent_colour=Theme.info)
        container.add_item(ui.TextDisplay(
            f"## {encounter.emoji} {encounter.name}\n{encounter.description}"
        ))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay("-# choose within 5 minutes — this is your hunt"))
        container.add_item(ui.ActionRow(*[
            EncounterChoiceButton(choice.key, choice.label, choice.emoji)
            for choice in encounter.choices
        ]))
        self.add_item(container)

    @staticmethod
    def resolution_lines(res: "EncounterResolution") -> list[str]:
        lines = [f"*{res.choice.flavor}*"]
        parts = []
        if res.coins:
            parts.append(f"**{res.coins:+,}** coins")
        if res.shards:
            parts.append(f"**+{res.shards}** shards")
        if res.xp:
            parts.append(f"**+{res.xp}** XP")
        if res.item is not None:
            parts.append(f"\U0001f081 {res.item}")
        lines.append(" \u00b7 ".join(parts) if parts else "*Nothing — but a story.*")
        if res.level_up:
            lines.append(f"\U0001f53c **LEVEL UP!** Now level **{res.level_up}**.")
        return lines


def _outcome_view(encounter: "EncounterSpec", lines: list[str]) -> ui.LayoutView:
    view, container = _base(Theme.success)
    container.add_item(ui.TextDisplay(f"## {encounter.emoji} {encounter.name} — resolved"))
    container.add_item(ui.Separator())
    container.add_item(ui.TextDisplay("\n".join(lines)))
    view.add_item(container)
    return view


def _base(accent: int) -> tuple[ui.LayoutView, ui.Container]:
    view = ui.LayoutView(timeout=300)
    container = ui.Container(accent_colour=accent)
    view.add_item(container)
    return view, container
