"""Registry loading game content from JSON + pluggable spawn algorithms.

The rarity system is intentionally *not* an enum any more: tiers, spawn
weights, colours and rewards are data. New tiers can be added (or their
spawn odds retuned) purely by editing ``data/rarities.json``.

Spawn algorithms are strategies registered by name; ``game.json`` picks
one via ``spawn.algorithm``. Registering a new algorithm is a single
function — no service changes required.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Protocol

from bot.models.items import EquipmentTemplate, GachaCard, HuntEnemy

CONTENT_DIR: Final[Path] = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RarityTier:
    """A rarity tier — fully data-driven."""

    key: str
    label: str
    tier: int
    weight: float
    colour: int
    emoji: str
    stat_multiplier: float
    value_range: tuple[int, int]
    shard_reward: int
    stars: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "stars", "\u2605" * self.tier)

    @property
    def sell_value(self) -> int:
        lo, hi = self.value_range
        return (lo + hi) // 2


@dataclass(frozen=True, slots=True)
class UpgradeSpec:
    key: str
    name: str
    description: str
    emoji: str
    base_cost: int
    cost_growth: float
    effect_per_level: float
    max_level: int = 25

    def cost(self, current_level: int) -> int:
        return int(self.base_cost * (self.cost_growth ** current_level))

    def effect(self, level: int) -> float:
        return self.effect_per_level * level


@dataclass(frozen=True, slots=True)
class BadgeSpec:
    key: str
    name: str
    emoji: str
    description: str
    scope: str = "guild"  # "guild" | "global"


# ---------------------------------------------------------------------------
# Spawn algorithms (pluggable strategies)
# ---------------------------------------------------------------------------

class SpawnAlgorithm(Protocol):
    def __call__(
        self,
        rng: random.Random,
        tiers: list[RarityTier],
        weights: list[float],
        luck: float,
    ) -> RarityTier: ...


def _weighted_luck(
    rng: random.Random,
    tiers: list[RarityTier],
    weights: list[float],
    luck: float,
) -> RarityTier:
    """Weighted roll; positive luck exponentially tilts towards high tiers."""
    tilted = [w * (1.0 + max(luck, 0.0)) ** (t.tier - 1) for w, t in zip(weights, tiers)]
    return rng.choices(tiers, weights=tilted, k=1)[0]


def _flat(
    rng: random.Random,
    tiers: list[RarityTier],
    weights: list[float],
    luck: float,
) -> RarityTier:
    """Uniform roll ignoring weights and luck (useful for events/testing)."""
    return rng.choice(tiers)


_SPAWN_ALGORITHMS: dict[str, SpawnAlgorithm] = {}


def register_spawn_algorithm(name: str) -> Callable[[SpawnAlgorithm], SpawnAlgorithm]:
    """Decorator registering a spawn strategy by name for game.json."""
    def decorator(fn: SpawnAlgorithm) -> SpawnAlgorithm:
        _SPAWN_ALGORITHMS[name] = fn
        return fn
    return decorator


register_spawn_algorithm("weighted_luck")(_weighted_luck)
register_spawn_algorithm("flat")(_flat)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ContentRegistry:
    """Immutable snapshot of all game content loaded from JSON."""

    def __init__(
        self,
        rarities: dict[str, RarityTier],
        cards: dict[str, GachaCard],
        equipment: dict[str, EquipmentTemplate],
        enemies: dict[str, HuntEnemy],
        upgrades: dict[str, UpgradeSpec],
        badges: dict[str, BadgeSpec],
        spawn_algorithm: str = "weighted_luck",
    ) -> None:
        self._rarities = rarities
        self._tier_order = sorted(rarities.values(), key=lambda r: r.tier)
        self._cards = cards
        self._equipment = equipment
        self._enemies = enemies
        self._upgrades = upgrades
        self._badges = badges
        self._spawn_algorithm = spawn_algorithm

    # -- construction --------------------------------------------------------

    @classmethod
    def load(cls, directory: Path = CONTENT_DIR, spawn_algorithm: str = "weighted_luck") -> "ContentRegistry":
        rarities_raw = _read_json(directory / "rarities.json")
        if not rarities_raw:
            raise ValueError(f"{directory / 'rarities.json'} contains no rarity tiers")
        rarities: dict[str, RarityTier] = {}
        for entry in rarities_raw:
            if entry["key"] in rarities:
                raise ValueError(f"duplicate rarity key '{entry['key']}' in rarities.json")
            lo, hi = entry["value_range"]
            rarities[entry["key"]] = RarityTier(
                key=entry["key"],
                label=entry["label"],
                tier=int(entry["tier"]),
                weight=float(entry["weight"]),
                colour=int(entry["colour"], 16),
                emoji=entry["emoji"],
                stat_multiplier=float(entry["stat_multiplier"]),
                value_range=(int(lo), int(hi)),
                shard_reward=int(entry.get("shard_reward", 1)),
            )

        cards: dict[str, GachaCard] = {}
        for entry in _read_json(directory / "cards.json"):
            if entry["rarity"] not in rarities:
                raise ValueError(
                    f"card '{entry['key']}' references unknown rarity '{entry['rarity']}'"
                )
            cards[entry["key"]] = GachaCard(
                key=entry["key"],
                name=entry["name"],
                rarity=rarities[entry["rarity"]],
                lore=entry.get("lore", ""),
                series=entry.get("series", "Standard"),
            )

        equipment: dict[str, EquipmentTemplate] = {}
        for entry in _read_json(directory / "equipment.json"):
            if entry.get("min_rarity", "common") not in rarities:
                raise ValueError(
                    f"equipment '{entry['key']}' references unknown min_rarity '{entry.get('min_rarity')}'"
                )
            equipment[entry["key"]] = EquipmentTemplate(
                key=entry["key"],
                name=entry["name"],
                slot=entry["slot"],
                base_attack=int(entry.get("base_attack", 0)),
                base_defense=int(entry.get("base_defense", 0)),
                base_luck=int(entry.get("base_luck", 0)),
                min_rarity=rarities[entry.get("min_rarity", "common")],
            )

        enemies: dict[str, HuntEnemy] = {}
        for entry in _read_json(directory / "enemies.json"):
            if entry["rarity"] not in rarities:
                raise ValueError(
                    f"enemy '{entry['key']}' references unknown rarity '{entry['rarity']}'"
                )
            enemies[entry["key"]] = HuntEnemy(
                key=entry["key"],
                name=entry["name"],
                rarity=rarities[entry["rarity"]],
                base_coins=int(entry["base_coins"]),
                base_xp=int(entry["base_xp"]),
            )

        upgrades: dict[str, UpgradeSpec] = {}
        for entry in _read_json(directory / "upgrades.json"):
            upgrades[entry["key"]] = UpgradeSpec(
                key=entry["key"],
                name=entry["name"],
                description=entry["description"],
                emoji=entry["emoji"],
                base_cost=int(entry["base_cost"]),
                cost_growth=float(entry["cost_growth"]),
                effect_per_level=float(entry["effect_per_level"]),
                max_level=int(entry.get("max_level", 25)),
            )

        badges: dict[str, BadgeSpec] = {}
        for entry in _read_json(directory / "badges.json"):
            badges[entry["key"]] = BadgeSpec(
                key=entry["key"],
                name=entry["name"],
                emoji=entry["emoji"],
                description=entry.get("description", ""),
                scope=entry.get("scope", "guild"),
            )

        return cls(rarities, cards, equipment, enemies, upgrades, badges, spawn_algorithm)

    # -- lifecycle -----------------------------------------------------------

    def swap(self, other: "ContentRegistry") -> None:
        """Adopt another registry's content in place.

        Services hold a reference to *this* instance, so swapping the
        internals (rather than the object) is what makes content hot
        reloads visible everywhere at once.
        """
        self._rarities = other._rarities
        self._tier_order = other._tier_order
        self._cards = other._cards
        self._equipment = other._equipment
        self._enemies = other._enemies
        self._upgrades = other._upgrades
        self._badges = other._badges
        self._spawn_algorithm = other._spawn_algorithm

    # -- rarity API ----------------------------------------------------------

    @property
    def rarities(self) -> list[RarityTier]:
        return list(self._tier_order)

    def rarity(self, key: str) -> RarityTier | None:
        return self._rarities.get(key)

    def rarity_or_raise(self, key: str) -> RarityTier:
        rarity = self._rarities.get(key)
        if rarity is None:
            raise KeyError(f"Unknown rarity '{key}' in content data")
        return rarity

    def tier(self, tier_number: int) -> RarityTier | None:
        """Rarity whose tier number is exactly `tier_number`, if present."""
        for rarity in self._tier_order:
            if rarity.tier == tier_number:
                return rarity
        return None

    @property
    def highest_tier(self) -> RarityTier:
        return self._tier_order[-1]

    @property
    def lowest_tier(self) -> RarityTier:
        return self._tier_order[0]

    def tier_at_most(self, tier: int) -> RarityTier:
        """Highest rarity whose tier is <= `tier` (clamped to range)."""
        eligible = [r for r in self._tier_order if r.tier <= max(tier, self.lowest_tier.tier)]
        return eligible[-1] if eligible else self.lowest_tier

    def roll_rarity(
        self,
        rng: random.Random,
        luck: float = 0.0,
        weight_overrides: dict[str, float] | None = None,
    ) -> RarityTier:
        """Roll a rarity using the configured spawn algorithm.

        `weight_overrides` lets callers (e.g. soft pity) adjust weights
        without knowing the algorithm.
        """
        weights = {
            r.key: (weight_overrides or {}).get(r.key, r.weight)
            for r in self._tier_order
        }
        algorithm = _SPAWN_ALGORITHMS.get(self._spawn_algorithm, _weighted_luck)
        return algorithm(
            rng,
            list(self._tier_order),
            [weights[r.key] for r in self._tier_order],
            luck,
        )

    # -- content collections -------------------------------------------------

    @property
    def cards(self) -> dict[str, GachaCard]:
        return dict(self._cards)

    def card(self, key: str) -> GachaCard | None:
        return self._cards.get(key)

    def all_cards(self) -> list[GachaCard]:
        return list(self._cards.values())

    def cards_by_rarity(self, rarity: RarityTier) -> list[GachaCard]:
        return [c for c in self._cards.values() if c.rarity.key == rarity.key]

    @property
    def equipment_templates(self) -> dict[str, EquipmentTemplate]:
        return dict(self._equipment)

    def equipment_template(self, key: str) -> EquipmentTemplate | None:
        return self._equipment.get(key)

    def all_equipment(self) -> list[EquipmentTemplate]:
        return list(self._equipment.values())

    def equipment_by_rarity(self, rarity: RarityTier) -> list[EquipmentTemplate]:
        return [e for e in self._equipment.values() if e.min_rarity.tier <= rarity.tier]

    @property
    def enemies(self) -> dict[str, HuntEnemy]:
        return dict(self._enemies)

    def enemy(self, key: str) -> HuntEnemy | None:
        return self._enemies.get(key)

    def all_enemies(self) -> list[HuntEnemy]:
        return list(self._enemies.values())

    def enemies_by_rarity(self, rarity: RarityTier) -> list[HuntEnemy]:
        return [e for e in self._enemies.values() if e.rarity.key == rarity.key]

    # -- upgrades / badges ---------------------------------------------------

    @property
    def upgrades(self) -> dict[str, UpgradeSpec]:
        return dict(self._upgrades)

    def upgrade(self, key: str) -> UpgradeSpec | None:
        return self._upgrades.get(key)

    def all_upgrades(self) -> list[UpgradeSpec]:
        return list(self._upgrades.values())

    def upgrade_effects(self) -> dict[str, float]:
        return {u.key: u.effect_per_level for u in self._upgrades.values()}

    @property
    def badges(self) -> dict[str, BadgeSpec]:
        return dict(self._badges)

    def badge(self, key: str) -> BadgeSpec | None:
        return self._badges.get(key)

    def all_badges(self) -> list[BadgeSpec]:
        return list(self._badges.values())


def _read_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
