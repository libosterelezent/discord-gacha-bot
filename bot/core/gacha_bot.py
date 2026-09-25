"""The GachaBot class: a `commands.Bot` subclass that owns the service
layer, wires cogs, installs a global error handler, and manages
startup/shutdown of background tasks.
"""
from __future__ import annotations

import logging
import random
import traceback
from typing import Any

import discord
from discord.ext import commands

from bot.config import CONFIG, CONSTANTS
from bot.core.database import Database
from bot.core.exceptions import GachaBotError
from bot.models.game_data import load_game_data
from bot.models.player import Player, StatProfile
from bot.services.economy import EconomyService
from bot.services.equipment import EquipmentService
from bot.services.gacha import GachaService
from bot.services.hunt import HuntService
from bot.services.huntbot import HuntBotService
from bot.services.upgrades import UpgradeCatalog, UpgradeService
from bot.utils.embeds import COLOUR_BAD, error_embed

logger = logging.getLogger("gacha.bot")


class GachaBot(commands.Bot):
    """Container for services + Discord integration."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(CONFIG.command_prefix),
            intents=intents,
            help_command=None,  # replaced by the Components V2 menu (bot.cogs.help_cog)
            case_insensitive=True,
        )

        load_game_data()
        self.rng = random.Random()
        self.db = Database(CONFIG.database_path)

        # service graph (economy first: others depend on it)
        self.economy = EconomyService(self.db)
        self.gacha = GachaService(self.db, rng=self.rng)
        self.equipment = EquipmentService(self.db)
        self.upgrades = UpgradeService(self.db, get_player=self.economy.ensure_player)
        self.hunt = HuntService(self.db, self.economy, rng=self.rng)
        self.huntbot = HuntBotService(self.db, rng=self.rng)
        self._services: tuple[Any, ...] = (
            self.economy, self.gacha, self.equipment, self.upgrades, self.hunt, self.huntbot,
        )

    # -- profile composition ------------------------------------------------

    async def player_profile(self, user_id: int) -> tuple[Player, StatProfile]:
        """Fetch the player aggregate and compose their effective stats."""
        player = await self.economy.ensure_player(user_id)
        equipped_rows = await self.db.fetch_all(
            "SELECT * FROM equipment WHERE user_id = ? AND equipped = 1", (str(user_id),)
        )
        from bot.models.items import Equipment, EquipmentType
        from bot.models.rarities import Rarity

        for row in equipped_rows:
            player.equipped[row["slot"]] = Equipment(
                db_id=row["id"], key=row["item_key"], name=row["item_key"].replace("_", " ").title(),
                etype=EquipmentType[row["slot"].upper()], rarity=Rarity.from_key(row["rarity"]) or Rarity.COMMON,
                attack=row["attack"], defense=row["defense"], luck=row["luck"],
                level=row["level"], equipped=True,
            )
        profile = StatProfile.compose(player, UpgradeCatalog.effects())
        return player, profile

    # -- lifecycle --------------------------------------------------------------

    async def setup_hook(self) -> None:
        await self.db.connect()
        for service in self._services:
            await service.on_start()
        for extension in (
            "bot.cogs.economy_cog",
            "bot.cogs.gacha_cog",
            "bot.cogs.hunt_cog",
            "bot.cogs.huntbot_cog",
            "bot.cogs.equipment_cog",
            "bot.cogs.upgrade_cog",
            "bot.cogs.admin_cog",
            "bot.cogs.help_cog",
        ):
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

    async def close(self) -> None:
        logger.info("Shutting down ...")
        await self.huntbot.close()
        for service in reversed(self._services):
            close = getattr(service, "close", None)
            if close:
                await close()
        await self.db.close()
        await super().close()

    # -- global error handling ------------------------------------------------------

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if hasattr(ctx.command, "on_error"):
            return
        error = getattr(error, "original", error)

        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(embed=error_embed(f"Missing argument: `{error.param.name}`."), mention_author=False)
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(embed=error_embed(str(error)), mention_author=False)
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                embed=error_embed(f"Slow down! Try again in **{error.retry_after:,.0f}s**."),
                mention_author=False,
            )
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.reply(embed=error_embed("You don't have permission to do that."), mention_author=False)
            return
        if isinstance(error, GachaBotError):
            await ctx.reply(embed=error_embed(str(error)), mention_author=False)
            return

        logger.error("Unhandled command error in %s\n%s", ctx.command, "".join(traceback.format_exception(error)))
        await ctx.reply(
            embed=error_embed("An unexpected error occurred. It has been logged."),
            mention_author=False,
        )

    async def on_app_command_error(self, interaction: discord.Interaction, error: Exception) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, GachaBotError):
            message = error_embed(str(error))
        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            message = error_embed(f"Slow down! Try again in **{error.retry_after:,.0f}s**.")
        else:
            logger.error("Unhandled app-command error\n%s", "".join(traceback.format_exception(error)))
            message = error_embed("An unexpected error occurred. It has been logged.")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=message, ephemeral=True)
            else:
                await interaction.response.send_message(embed=message, ephemeral=True)
        except discord.HTTPException:
            pass
