"""The GachaBot class: a `commands.Bot` subclass that owns the service
graph, content registry, event bus and observability sink; wires cogs,
installs global error handlers, and manages startup/shutdown of
background tasks.
"""
from __future__ import annotations

import logging
import random
import traceback
from typing import Any

import discord
from discord.ext import commands

from bot.config import CONFIG, SETTINGS
from bot.content.registry import ContentRegistry
from bot.core.cooldowns import CooldownManager
from bot.core.database import Database
from bot.core.events import EventBus, GameEvent
from bot.core.exceptions import GachaBotError
from bot.observability.discord_sink import DiscordSink
from bot.services.badges import BadgeService
from bot.services.economy import EconomyService
from bot.services.equipment import EquipmentService
from bot.services.gacha import GachaService
from bot.services.hunt import HuntService
from bot.services.huntbot import HuntBotService
from bot.services.upgrades import UpgradeService
from bot.ui.theme import Theme

logger = logging.getLogger("gacha.bot")

EXTENSIONS: tuple[str, ...] = (
    "bot.cogs.economy_cog",
    "bot.cogs.gacha_cog",
    "bot.cogs.hunt_cog",
    "bot.cogs.huntbot_cog",
    "bot.cogs.equipment_cog",
    "bot.cogs.upgrade_cog",
    "bot.cogs.admin_cog",
    "bot.cogs.help_cog",
)


class GachaBot(commands.Bot):
    """Container for content + services + observability + Discord glue."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(CONFIG.command_prefix),
            intents=intents,
            help_command=None,  # replaced by the Components V2 menu (bot.ui.help)
            case_insensitive=True,
        )

        # content + infrastructure
        self.content = ContentRegistry.load(spawn_algorithm=SETTINGS.spawn.algorithm)
        self.rng = random.Random()
        self.db = Database(CONFIG.database_url)
        self.bus = EventBus()
        self.cooldowns = CooldownManager(SETTINGS)
        self.sink = DiscordSink(self, self.db, self.bus, CONFIG.maintainer_guild_id)

        # service graph (economy first: others depend on it)
        self.economy = EconomyService(self.db, self.content, SETTINGS, self.bus, self.cooldowns)
        self.gacha = GachaService(self.db, self.content, SETTINGS, self.bus, self.cooldowns)
        self.equipment = EquipmentService(self.db, self.content, SETTINGS, self.bus, self.cooldowns)
        self.upgrades = UpgradeService(
            self.db, self.content, SETTINGS, self.bus, self.cooldowns,
            get_player=self.economy.ensure_player,
        )
        self.hunt = HuntService(
            self.db, self.content, SETTINGS, self.bus, self.cooldowns, economy=self.economy
        )
        self.huntbot = HuntBotService(self.db, self.content, SETTINGS, self.bus, self.cooldowns)
        self.badges = BadgeService(self.db, self.content, SETTINGS, self.bus, self.cooldowns)
        self._services: tuple[Any, ...] = (
            self.economy, self.gacha, self.equipment, self.upgrades,
            self.hunt, self.huntbot, self.badges,
        )

    # -- settings ------------------------------------------------------------------

    def reload_settings(self) -> None:
        """Reload game.json in place.

        Services and the cooldown manager hold a *reference* to the
        SETTINGS object, which reloads its sections in place — so every
        component picks up new values immediately without rewiring.
        """
        SETTINGS.reload()

    # -- profile composition --------------------------------------------------------

    async def player_profile(self, guild_id: int | None, user_id: int) -> tuple[Any, Any]:
        """Fetch the player aggregate and compose their effective stats."""
        from bot.models.items import Equipment, EquipmentType
        from bot.models.player import StatProfile

        player = await self.economy.ensure_player(guild_id, user_id)
        equipped_rows = await self.db.fetch_all(
            "SELECT * FROM equipment WHERE guild_id = :g AND user_id = :u AND equipped = 1",
            {"g": player.guild_id, "u": user_id},
        )
        for row in equipped_rows:
            rarity = self.content.rarity(row["rarity"])
            if rarity is None:
                continue
            player.equipped[row["slot"]] = Equipment(
                db_id=row["id"], key=row["item_key"], name=row["item_key"].replace("_", " ").title(),
                etype=EquipmentType.from_key(row["slot"]) or EquipmentType.WEAPON,
                rarity=rarity,
                attack=row["attack"], defense=row["defense"], luck=row["luck"],
                level=row["level"], equipped=True,
            )
        profile = StatProfile.compose(player, self.content.upgrade_effects())
        return player, profile

    # -- lifecycle --------------------------------------------------------------

    async def setup_hook(self) -> None:
        await self.db.connect()
        for service in self._services:
            await service.on_start()
        await self.sink.start()
        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Loaded extension %s", extension)
            except commands.ExtensionError:
                logger.exception("Failed to load extension %s", extension)
        try:
            synced = await self.tree.sync()
            logger.info("Synced %d application commands", len(synced))
        except (discord.HTTPException, discord.ClientException) as exc:
            # MissingApplicationID when running without a token (e.g. tooling).
            logger.warning("Slash-command sync deferred: %s", exc)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%d guilds)", self.user, len(self.guilds))
        await self.change_presence(
            activity=discord.CustomActivity(name=f"{Theme.bot_name} — {Theme.tagline}")
        )

    async def close(self) -> None:
        logger.info("Shutting down ...")
        await self.sink.stop()
        await self.huntbot.close()
        for service in reversed(self._services):
            await service.close()
        await self.db.close()
        await super().close()

    # -- error publication -----------------------------------------------------------

    async def _report_error(self, source: str, message: str, error: BaseException) -> None:
        logger.error("Unhandled %s error\n%s", source, "".join(traceback.format_exception(error)))
        await self.bus.publish(
            GameEvent(
                category="error", action=source, message=f"{message}\n```\n{type(error).__name__}: {error}\n```",
            )
        )

    # -- global error handling ------------------------------------------------------

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if hasattr(ctx.command, "on_error"):
            return
        error = getattr(error, "original", error)

        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(embed=Theme.error_embed(f"Missing argument: `{error.param.name}`."), mention_author=False)
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(embed=Theme.error_embed(str(error)), mention_author=False)
            return
        if isinstance(error, (commands.CommandOnCooldown,)):
            await ctx.reply(
                embed=Theme.error_embed(f"Slow down! Try again in **{error.retry_after:,.0f}s**."),
                mention_author=False,
            )
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.reply(embed=Theme.error_embed("You don't have permission to do that."), mention_author=False)
            return
        if isinstance(error, GachaBotError):
            await ctx.reply(embed=Theme.error_embed(str(error)), mention_author=False)
            return

        await self._report_error("command", f"in `{ctx.command}`", error)
        await ctx.reply(
            embed=Theme.error_embed("An unexpected error occurred. It has been logged."),
            mention_author=False,
        )

    async def on_app_command_error(self, interaction: discord.Interaction, error: Exception) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, GachaBotError):
            message = Theme.error_embed(str(error))
        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            message = Theme.error_embed(f"Slow down! Try again in **{error.retry_after:,.0f}s**.")
        else:
            await self._report_error(
                "app_command", f"in `/{interaction.command.qualified_name if interaction.command else '?'}`", error
            )
            message = Theme.error_embed("An unexpected error occurred. It has been logged.")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=message, ephemeral=True)
            else:
                await interaction.response.send_message(embed=message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_error(self, event_method: str, *args: object, **kwargs: object) -> None:
        # last-resort net for non-command event handlers (on_message etc.)
        exc = args[1] if len(args) > 1 and isinstance(args[1], BaseException) else None
        if exc is not None:
            await self._report_error("event", f"in `{event_method}`", exc)
        else:
            logger.exception("Unhandled error in event %s", event_method)
