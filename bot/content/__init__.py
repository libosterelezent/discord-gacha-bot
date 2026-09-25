"""Data-driven game content registry.

All game content — rarity tiers (including spawn weights), gacha cards,
equipment templates, hunt enemies, upgrades and badges — lives in JSON
files under ``bot/content/data/``. The registry loads them once and
exposes typed lookups, so **adding content never touches commands or
services**: drop a new entry in the JSON and reload.
"""
from bot.content.registry import (
    BadgeSpec,
    ContentRegistry,
    RarityTier,
    UpgradeSpec,
    register_spawn_algorithm,
)

__all__ = [
    "BadgeSpec",
    "ContentRegistry",
    "RarityTier",
    "UpgradeSpec",
    "register_spawn_algorithm",
]
