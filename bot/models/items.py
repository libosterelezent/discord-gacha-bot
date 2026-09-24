"""Item model definitions.

* :class:`EquipmentType` — slots (weapon/armor/amulet)
* :class:`Equipment`     — a concrete, rolled equipment instance
* :class:`GachaCard`     — collectible characters pulled from gacha
* :class:`ItemRegistry`  — metaclass-powered global registry so item
  definitions are declared once and looked up everywhere by key.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import ClassVar, Iterator

from bot.models.rarities import Rarity


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


@dataclass(frozen=True, slots=True)
class EquipmentTemplate:
    """Blueprint for a piece of equipment (immutable)."""

    key: str
    name: str
    etype: EquipmentType
    base_attack: int
    base_defense: int
    base_luck: int
    min_rarity: Rarity = Rarity.COMMON

    def roll(self, rng: random.Random, rarity: Rarity) -> "Equipment":
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
    rarity: Rarity
    attack: int
    defense: int
    luck: int
    level: int = 0
    equipped: bool = False
    db_id: int | None = None

    # Each +1 level adds 12% to all stats (compounding).
    UPGRADE_GROWTH: ClassVar[float] = 0.12

    @property
    def display_name(self) -> str:
        stars = "+" * self.level if self.level else ""
        return f"{self.rarity.emoji} {self.name}{stars}"

    @property
    def power(self) -> int:
        return self.attack * 2 + self.defense * 2 + self.luck * 3

    def stats_at_level(self, level: int) -> tuple[int, int, int]:
        factor = (1 + self.UPGRADE_GROWTH) ** level
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
    rarity: Rarity
    lore: str = ""
    series: str = "Standard"

    @property
    def sell_value(self) -> int:
        lo, hi = self.rarity.value_range
        return (lo + hi) // 2


@dataclass(frozen=True, slots=True)
class HuntEnemy:
    """Enemy encountered during a hunt."""

    key: str
    name: str
    rarity: Rarity
    base_coins: int
    base_xp: int


class _RegistryMeta(type):
    """Metaclass collecting every declared instance into `cls._items`."""

    def __new__(mcs, name: str, bases: tuple[type, ...], namespace: dict) -> "_RegistryMeta":
        cls = super().__new__(mcs, name, bases, namespace)
        cls._items = {}
        return cls

    def register(cls, item) -> None:
        cls._items[item.key] = item

    def get(cls, key: str):
        return cls._items.get(key)

    def __iter__(cls) -> Iterator:
        return iter(cls._items.values())

    def __contains__(cls, key: str) -> bool:
        return key in cls._items

    def __len__(cls) -> int:
        return len(cls._items)


class ItemRegistry(metaclass=_RegistryMeta):
    """Namespace holding the global item registries."""

    _items: ClassVar[dict] = {}

    cards: ClassVar[dict[str, GachaCard]] = {}
    equipment: ClassVar[dict[str, EquipmentTemplate]] = {}
    enemies: ClassVar[dict[str, HuntEnemy]] = {}

    @classmethod
    def card(cls, key: str) -> GachaCard | None:
        return cls.cards.get(key)

    @classmethod
    def equipment_template(cls, key: str) -> EquipmentTemplate | None:
        return cls.equipment.get(key)

    @classmethod
    def enemy(cls, key: str) -> HuntEnemy | None:
        return cls.enemies.get(key)

    @classmethod
    def all_cards(cls) -> list[GachaCard]:
        return list(cls.cards.values())

    @classmethod
    def all_equipment(cls) -> list[EquipmentTemplate]:
        return list(cls.equipment.values())

    @classmethod
    def all_enemies(cls) -> list[HuntEnemy]:
        return list(cls.enemies.values())

    @classmethod
    def cards_by_rarity(cls, rarity: Rarity) -> list[GachaCard]:
        return [c for c in cls.cards.values() if c.rarity == rarity]

    @classmethod
    def equipment_by_rarity(cls, rarity: Rarity) -> list[EquipmentTemplate]:
        return [e for e in cls.equipment.values() if e.min_rarity <= rarity]

    @classmethod
    def enemies_by_rarity(cls, rarity: Rarity) -> list[HuntEnemy]:
        return [e for e in cls.enemies.values() if e.rarity == rarity]
