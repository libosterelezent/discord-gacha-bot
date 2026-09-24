"""Rarity system: an IntEnum carrying weights, multipliers, colours and
metadata. Enum methods keep rarity logic centralised instead of being
scattered across services.
"""
from __future__ import annotations

import random
from enum import IntEnum
from typing import ClassVar


class Rarity(IntEnum):
    """Ordered by power; ``int(rarity)`` doubles as a strength tier."""

    COMMON = 1
    UNCOMMON = 2
    RARE = 3
    EPIC = 4
    LEGENDARY = 5
    MYTHIC = 6

    # ClassVar: shared across instances, not per-member state.
    __WEIGHTS__: ClassVar[dict["Rarity", float]] = {
        COMMON: 550,
        UNCOMMON: 250,
        RARE: 130,
        EPIC: 55,
        LEGENDARY: 13,
        MYTHIC: 2,
    }

    @property
    def key(self) -> str:
        return self.name.lower()

    @property
    def stars(self) -> str:
        return "\u2605" * self.value  # ★

    @property
    def colour(self) -> int:
        return {
            Rarity.COMMON: 0x9E9E9E,
            Rarity.UNCOMMON: 0x4CAF50,
            Rarity.RARE: 0x2196F3,
            Rarity.EPIC: 0x9C27B0,
            Rarity.LEGENDARY: 0xFFB300,
            Rarity.MYTHIC: 0xFF1744,
        }[self]

    @property
    def emoji(self) -> str:
        return {
            Rarity.COMMON: "\u26ab",          # ⚫
            Rarity.UNCOMMON: "\U0001f7e9",  # 🟩
            Rarity.RARE: "\U0001f7e6",      # 🟦
            Rarity.EPIC: "\U0001f7e3",      # 🟪
            Rarity.LEGENDARY: "\u2b50",       # ⭐
            Rarity.MYTHIC: "\U0001f531",    # 🔱
        }[self]

    @property
    def stat_multiplier(self) -> float:
        return {1: 1.0, 2: 1.4, 3: 2.0, 4: 3.0, 5: 4.5, 6: 6.5}[self.value]

    @property
    def value_range(self) -> tuple[int, int]:
        """Sell price range in coins."""
        return {
            Rarity.COMMON: (15, 40),
            Rarity.UNCOMMON: (50, 120),
            Rarity.RARE: (150, 350),
            Rarity.EPIC: (400, 900),
            Rarity.LEGENDARY: (1_000, 2_500),
            Rarity.MYTHIC: (3_000, 7_500),
        }[self]

    @classmethod
    def weights(cls) -> dict["Rarity", float]:
        return dict(cls.__WEIGHTS__)

    @classmethod
    def roll(cls, rng: random.Random, luck: float = 0.0) -> "Rarity":
        """Weighted roll. Positive `luck` (0..1) biases towards higher
        tiers by re-normalising weights with an exponential tilt."""
        members = list(cls)
        weights = [cls.__WEIGHTS__[r] * (1.0 + luck) ** (r.value - 1) for r in members]
        return rng.choices(members, weights=weights, k=1)[0]

    @classmethod
    def from_key(cls, key: str) -> "Rarity | None":
        try:
            return cls[key.upper()]
        except KeyError:
            return None
