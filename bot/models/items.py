"""Item model definitions.

* :class:`EquipmentType`  — slots (weapon/armor/amulet)
* :class:`EquipmentTemplate` — blueprint loaded from content JSON
* :class:`Equipment`      — a concrete, rolled equipment instance
* :class:`GachaCard`      — collectible characters pulled from gacha
* :class:`HuntEnemy`      — enemy encountered during a hunt

Rarities are *values from the content registry* (see
``bot.content.registry.RarityTier``), not enum members, so content can
grow without code changes.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.content.registry import RarityTier


class EquipmentType(Enum):
    WEAPON = auto()
    ARMOR = auto()
    AMULET = auto()

    @property
    def emoji(self) -> str:
        return {EquipmentType.WEAPON: "\u2694\ufe0f", EquipmentType.ARMOR: "\U0001f6e1\ufe0f", EquipmentType.AMULET: "\U0001f4ff"}[self]

    @property
    def key(self) -> str:
        return self.name.lower()

    @classmethod
    def from_key(cls, key: str) -> "EquipmentType | None":
        try:
            return cls[key.upper()]
        except KeyError:
            return None


@dataclass(frozen=True, slots=True)
class EquipmentTemplate:
    """Blueprint for a piece of equipment (immutable, loaded from JSON)."""

    key: str
    name: str
    slot: str                       # "weapon" | "armor" | "amulet"
    base_attack: int
    base_defense: int
    base_luck: int
    min_rarity: "RarityTier"

    @property
    def etype(self) -> EquipmentType:
        return EquipmentType.from_key(self.slot) or EquipmentType.WEAPON

    def roll(self, rng: random.Random, rarity: "RarityTier") -> "Equipment":
        """Roll a concrete instance at `rarity` with small variance."""
        spread = 0.85 + rng.random() * 0.3  # 85%..115% of base
        mult = rarity.stat_multiplier
        return Equipment(
            key=self.key,
            name=self.name,
            etype=self.etype,
            rarity=rarity,
            attack=max(1, round(self.base_attack * mult * spread)),
            defense=max(0, round(self.base_defense * mult * spread)),
            luck=max(0, round(self.base_luck * mult * spread)),
        )


@dataclass(slots=True)
class Equipment:
    """An owned equipment instance (mutable: can be upgraded)."""

    key: str
    name: str
    etype: EquipmentType
    rarity: "RarityTier"
    attack: int
    defense: int
    luck: int
    level: int = 0
    equipped: bool = False
    db_id: int | None = None

    # Forge growth per +1 level lives in game.json (equipment.forge_growth)
    # and is read at call time so !reload_settings retunes it live.

    @property
    def display_name(self) -> str:
        stars = "+" * self.level if self.level else ""
        return f"{self.rarity.emoji} {self.name}{stars}"

    @property
    def power(self) -> int:
        return self.attack * 2 + self.defense * 2 + self.luck * 3

    def stats_at_level(self, level: int) -> tuple[int, int, int]:
        from bot.config import SETTINGS

        factor = (1 + SETTINGS.equipment.forge_growth) ** level
        return round(self.attack * factor), round(self.defense * factor), round(self.luck * factor)

    def apply_level(self, level: int) -> None:
        self.attack, self.defense, self.luck = self.stats_at_level(level)
        self.level = level

    def __str__(self) -> str:
        return f"{self.display_name} [{self.etype.emoji} {self.etype.key}] \u2694{self.attack} \U0001f6e1{self.defense} \U0001f380{self.luck}"


@dataclass(frozen=True, slots=True)
class GachaCard:
    """Collectible gacha card (character). Immutable value object."""

    key: str
    name: str
    rarity: "RarityTier"
    lore: str = ""
    series: str = "Standard"

    @property
    def sell_value(self) -> int:
        return self.rarity.sell_value


@dataclass(frozen=True, slots=True)
class HuntEnemy:
    """Enemy encountered during a hunt."""

    key: str
    name: str
    rarity: "RarityTier"
    base_coins: int
    base_xp: int
