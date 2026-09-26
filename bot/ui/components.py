"""Components V2 builders for rich, interactive views.

Discord's Components V2 (``ui.LayoutView`` / ``ui.Container`` etc.) is
used wherever a message benefits from structure — profile cards, pull
results, hunts, leaderboards, collections. Simple action confirmations
stay **plain text messages** (see :func:`plain`), keeping noise low in
busy channels.

All views take their accent colours and footer text from
:class:`bot.ui.theme.Theme` — no scattered strings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

import discord
from discord import ui

from bot.ui.theme import Theme

if TYPE_CHECKING:
    from bot.models.player import Player, StatProfile
    from bot.services.gacha import PullSession
    from bot.services.hunt import HuntResult
    from bot.services.huntbot import HuntBotState


def plain(text: str) -> str:
    """Marker for call sites intentionally using a plain text reply."""
    return text


class SharePullButton(ui.Button):
    """Reposts a pull highlight into the channel for everyone to see."""

    def __init__(self, highlight: str, colour: int, custom_note: str = "") -> None:
        super().__init__(
            label="Share", emoji="\U0001f4e4",
            style=discord.ButtonStyle.secondary, row=0,
        )
        self._highlight = highlight
        self._colour = colour
        self._note = custom_note
        self._shared = False

    async def callback(self, interaction: discord.Interaction) -> None:
        if self._shared:
            await interaction.response.send_message(
                "Already shared — spread the luck around.", ephemeral=True
            )
            return
        self._shared = True
        embed = Theme.embed(description=self._highlight, colour=self._colour)
        if self._note:
            embed.set_footer(text=self._note)
        await interaction.response.send_message(embed=embed)
        try:
            self.disabled = True
        except Exception:  # pragma: no cover - view already detached
            pass


def base_container(accent: int | None = None) -> tuple[ui.LayoutView, ui.Container]:
    """Create a LayoutView + themed Container pair."""
    view = ui.LayoutView(timeout=300)
    container = ui.Container(accent_colour=accent if accent is not None else Theme.primary)
    view.add_item(container)
    return view, container


def _header(container: ui.Container, text: str, thumbnail: str | None = None) -> None:
    if thumbnail:
        container.add_item(
            ui.Section(text, accessory=ui.Thumbnail(media=thumbnail, description=Theme.footer()))
        )
    else:
        container.add_item(ui.TextDisplay(text))


def card_view(
    header: str,
    body: str,
    accent: int | None = None,
    footer: str | None = None,
    thumbnail: str | None = None,
) -> ui.LayoutView:
    """Generic rich card: header / body / footer inside one container."""
    view, container = base_container(accent)
    _header(container, header, thumbnail)
    container.add_item(ui.Separator())
    if body:
        container.add_item(ui.TextDisplay(body))
    if footer:
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(f"-# {footer}"))
    return view


# ---------------------------------------------------------------------------
# Game views
# ---------------------------------------------------------------------------

def profile_view(
    player: "Player",
    profile: "StatProfile",
    target_name: str,
    avatar_url: str | None,
    huntbot: "HuntBotState | None" = None,
    badges: Sequence[str] | None = None,
    battery_capacity: int = 24,
    pity_limit: int = 90,
    set_lines: Sequence[str] | None = None,
) -> ui.LayoutView:
    xp_now, xp_next = player.xp_progress()
    stats = "\n".join(profile.summary_lines())

    grid = (
        f"### Wallet\n{player.balance:,} coins \u00b7 {player.shards:,} shards\n"
        f"### Progress\nPower **{profile.power:,}** \u00b7 XP **{xp_now:,}/{xp_next:,}** \u00b7 "
        f"Pulls **{player.total_pulls:,}** \u00b7 Pity **{player.pity_counter}/{pity_limit}**"
    )

    extra: list[str] = []
    if profile.set_titles:
        extra.append("### Card Sets\n" + "\n".join(f"\u2022 {t}" for t in profile.set_titles))
    if set_lines:
        extra.append("\n".join(set_lines))
    if huntbot is not None:
        status = "\U0001f7e2 Active" if huntbot.active else "\U0001f534 Idle"
        extra.append(
            f"### \U0001f916 Huntbot \u2014 Lv.{huntbot.level} {status}\n"
            f"Battery {huntbot.battery}/{battery_capacity} \u00b7 Banked {huntbot.unclaimed_coins:,} coins"
        )
    if badges:
        extra.append(f"### Badges\n{' '.join(badges)}")

    body = stats + "\n\n" + grid
    if extra:
        body += "\n" + "\n".join(extra)

    return card_view(
        header=f"## \U0001f4dd {target_name} \u2014 Level {player.level}",
        body=body,
        accent=Theme.primary,
        footer=Theme.footer_with_tagline(),
        thumbnail=avatar_url,
    )


def pull_view(session: "PullSession", shard_emoji: str, pity_limit: int, puller: str | None = None) -> ui.LayoutView:
    outcomes = [o.describe() for o in session.outcomes]
    pity_note = " \U0001f6a8 **PITY!**" if any(o.pity_triggered for o in session.outcomes) else ""
    header = f"## \U0001f3a3 Pull Results x{len(session.outcomes)}{pity_note}"
    body = "\n".join(outcomes)
    footer = (
        f"New {session.new_cards} \u00b7 +{session.shards_gained}{shard_emoji} \u00b7 best: {session.best.label}"
    )
    view, container = base_container(session.best.colour if session.best else Theme.primary)
    _header(container, header)
    container.add_item(ui.Separator())
    container.add_item(ui.TextDisplay(body))
    container.add_item(ui.Separator())
    container.add_item(ui.TextDisplay(f"-# {footer}"))
    # share button for notable pulls (epic+): spreads the pull into the channel
    if session.best is not None and session.best.tier >= 4:
        best_outcome = max(session.outcomes, key=lambda o: o.rarity.tier)
        container.add_item(
            ui.ActionRow(
                SharePullButton(
                    highlight=best_outcome.describe(),
                    colour=best_outcome.rarity.colour,
                    custom_note=f"pulled by {puller}" if puller else Theme.footer(),
                )
            )
        )
    return view


def hunt_view(
    result: "HuntResult",
    money_emoji: str,
) -> ui.LayoutView:
    lines = [result.headline]
    if result.success:
        lines.append(f"Reward: {money_emoji} **{result.coins:,}** \u00b7 +{result.xp} XP")
    else:
        lines.append(f"Scavenged {money_emoji} **{result.coins:,}** \u00b7 +{result.xp} XP")
    lines.append(f"-# your power **{result.power:,}** vs enemy **{result.enemy_power:,}**")
    body = "\n".join(lines)
    footer = Theme.footer()
    if result.modifier is not None:
        footer = f"Today: {result.modifier.emoji} {result.modifier.name} — {result.modifier.description}"
    return card_view(
        f"## \U0001f3af The Hunt \u2014 {result.enemy.name}",
        body,
        accent=result.enemy.rarity.colour,
        footer=footer,
    )


def leaderboard_view(title: str, lines: Sequence[str]) -> ui.LayoutView:
    return card_view(f"## \U0001f3c6 {title}", "\n".join(lines), accent=Theme.primary)


def equipment_view(owner: str, lines: Sequence[str], note: str | None = None) -> ui.LayoutView:
    body = "\n".join(lines) if lines else "*No equipment yet.*"
    return card_view(f"## \U0001f392 {owner}'s Equipment", body, footer=note)


def collection_view(
    owner: str,
    lines: Sequence[str],
    owned: int,
    total: int,
    set_lines: Sequence[str] | None = None,
) -> ui.LayoutView:
    header = f"## \U0001f4d6 {owner}'s Collection"
    body = f"**{owned}**/{total} unique cards\n\n" + ("\n".join(lines) if lines else "*Nothing yet — go pull!*")
    if set_lines:
        body += "\n\n" + "\n".join(set_lines)
    return card_view(header, body, accent=Theme.primary)


def huntbot_view(state: "HuntBotState", income_per_tick: int, next_price: str, battery_capacity: int) -> ui.LayoutView:
    status = "\U0001f7e2 **Active**" if state.active else "\U0001f534 **Idle**"
    body = (
        f"Status: {status}\n"
        f"Battery: **{state.battery}**/{battery_capacity} "
        f"({'full \u2014 collect!' if state.battery >= battery_capacity else 'charging'})\n"
        f"Income/cycle: **~{income_per_tick:,}** coins\n"
        f"Banked: **{state.unclaimed_coins:,}** coins\n"
        f"Total hunts: **{state.hunts_done:,}**\n"
        f"Next upgrade: {next_price}"
    )
    return card_view(
        f"## \U0001f916 Huntbot \u2014 Level {state.level}",
        body,
        accent=Theme.primary,
        footer=Theme.footer(),
    )


def upgrades_view(lines: Sequence[str], buy_hint: str) -> ui.LayoutView:
    return card_view(
        "## \U0001f6e0\ufe0f Upgrade Workshop",
        "\n".join(lines),
        accent=Theme.primary,
        footer=buy_hint,
    )


def shard_info_view(balance: int, explanation: str) -> ui.LayoutView:
    return card_view(
        "## \u2728 Shards",
        f"You have **{balance:,}** shards.\n\n{explanation}",
        accent=Theme.primary,
        footer=Theme.footer(),
    )
