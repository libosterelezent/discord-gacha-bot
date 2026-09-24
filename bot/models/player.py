"""Player aggregate model + computed stat profile.

The `StatProfile` is a derived read-model: it composes base stats with
equipment bonuses and upgrade effects (composition over inheritance).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from bot.config import CONFIG, CONSTANTS
from bot.models.items import Equipment
from bot.models.rarities import Rarity


@dataclass(slots=True)
class Player:
    """Persistent player aggregate loaded from the database."""

    user_id: int
    balance: int = 0
    shards: int = 0
    xp: int = 0
    level: int = 1
    total_pulls: int = 0
    pity_counter: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Non-persistent helpers filled by services:
    equipped: dict[str, Equipment] = field(default_factory=dict)
    upgrades: dict[str, int] = field(default_factory=dict)

    def add_xp(self, amount: int) -> bool:
        """Add XP; level up as needed. Returns True if levelled up."""
        self.xp += amount
        levelled = False
        while self.xp >= self.xp_to_next:
            self.xp -= self.xp_to_next
            self.level += 1
            levelled = True
        return levelled

    @property
    def xp_to_next(self) -> int:
        base = CONSTANTS.xp_curve_base
        return int(100 * (base ** (self.level - 1)))

    def xp_progress(self) -> tuple[int, int]:
        return self.xp, self.xp_to_next


@dataclass(frozen=True, slots=True)
class StatProfile:
    """Immutable snapshot of a player's effective combat/luck stats."""

    attack: int
    defense: int
    luck: float          # 0..1+ used by RNG tilts
    coin_multiplier: float
    cooldown_multiplier: float
    equipped_items: tuple[Equipment, ...]

    @classmethod
    def compose(cls, player: Player, upgrade_effects: dict[str, float]) -> "StatProfile":
        """Factory: derive effective stats from player state.

        `upgrade_effects` maps upgrade keys to their per-level bonus,
        e.g. {"luck": 0.02, "coin_boost": 0.05, "swift": 0.01}.
        """
        atk = dfn = luk = 0
        equipped: list[Equipment] = []
        for item in player.equipped.values():
            atk += item.attack
            dfn += item.defense
            luk += item.luck
            equipped.append(item)

        luck_rating = luk + player.upgrades.get("luck", 0) * 2
        base_luck = luck_rating * 0.005                     # every luck point: +0.5%
        bonus_luck = player.upgrades.get("luck", 0) * upgrade_effects.get("luck", 0.0)
        coin_mult = 1.0 + player.level * 0.01 + player.upgrades.get("greed", 0) * upgrade_effects.get("greed", 0.0)
        cd_mult = max(0.4, 1.0 - player.upgrades.get("swiftness", 0) * upgrade_effects.get("swiftness", 0.0))

        return cls(
            attack=atk,
            defense=dfn,
            luck=min(base_luck + bonus_luck, 1.5),  # hard cap 150%
            coin_multiplier=coin_mult,
            cooldown_multiplier=cd_mult,
            equipped_items=tuple(equipped),
        )

    @property
    def power(self) -> int:
        return round(self.attack * 2 + self.defense * 2)

    def summary_lines(self) -> list[str]:
        lines = [
            f"\u2694\ufe0f Attack: **{self.attack}**",
            f"\U0001f6e1\ufe0f Defense: **{self.defense}**",
            f"\U0001f380 Luck: **{self.luck * 100:.1f}%**",
            f"\U0001f999 Coin bonus: **x{self.coin_multiplier:.2f}**",
        ]
        if self.equipped_items:
            lines.append("__Equipped:__")
            lines.extend(f"\u2022 {e}" for e in sorted(self.equipped_items, key=lambda x: x.etype.name))
        else:
            lines.append("*No equipment equipped.*")
        return lines
