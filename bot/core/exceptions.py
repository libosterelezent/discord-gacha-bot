"""Custom exception hierarchy for the gacha bot.

Every service raises these instead of raw exceptions, letting the
command layer map failures to user-friendly messages uniformly.
"""
from __future__ import annotations

from typing import Any


class GachaBotError(Exception):
    """Root of all domain errors raised by the bot."""

    default_message: str = "Something went wrong."

    def __init__(self, message: str | None = None, *args: Any) -> None:
        self.message = message or self.default_message
        super().__init__(self.message, *args)

    def __str__(self) -> str:
        return self.message


# --------------------------------------------------------------------------
# Player / economy
# --------------------------------------------------------------------------
class PlayerNotFoundError(GachaBotError):
    default_message = "Player profile not found."


class InsufficientFundsError(GachaBotError):
    default_message = "You do not have enough coins."

    def __init__(self, needed: int, available: int) -> None:
        super().__init__(f"You need **{needed:,}** coins but only have **{available:,}**.")
        self.needed = needed
        self.available = available


class NegativeAmountError(GachaBotError):
    default_message = "Amount must be a positive number."


# --------------------------------------------------------------------------
# Inventory / equipment
# --------------------------------------------------------------------------
class ItemNotFoundError(GachaBotError):
    default_message = "That item does not exist."

    def __init__(self, item_name: str = "") -> None:
        super().__init__(f"Item `{item_name}` not found." if item_name else self.default_message)
        self.item_name = item_name


class NotEnoughItemsError(GachaBotError):
    default_message = "You do not own that item (or not enough copies)."


class EquipmentError(GachaBotError):
    default_message = "Invalid equipment operation."


# --------------------------------------------------------------------------
# Cooldowns / gacha
# --------------------------------------------------------------------------
class CooldownError(GachaBotError):
    default_message = "You are doing that too fast."

    def __init__(self, retry_after: float, unit: str = "seconds") -> None:
        pretty = f"{retry_after:,.0f}" if unit == "seconds" else f"{retry_after:,.1f}"
        super().__init__(f"Slow down! Try again in **{pretty} {unit}**.")
        self.retry_after = retry_after


class GachaError(GachaBotError):
    default_message = "Gacha pull failed."


class UpgradeError(GachaBotError):
    default_message = "Invalid upgrade operation."

    def __init__(self, message: str | None = None, *, upgrade_key: str = "", level: int = 0) -> None:
        super().__init__(message)
        self.upgrade_key = upgrade_key
        self.level = level


class HuntBotError(GachaBotError):
    default_message = "Huntbot operation failed."


# --------------------------------------------------------------------------
# Infrastructure
# --------------------------------------------------------------------------
class DatabaseError(GachaBotError):
    default_message = "A database error occurred."

    def __init__(self, message: str | None = None, original: Exception | None = None) -> None:
        super().__init__(message)
        self.original = original
        if original is not None:
            self.__cause__ = original
