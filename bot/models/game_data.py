"""Static game content: gacha cards, equipment templates, enemies.

Importing this module registers everything into ``ItemRegistry``.
Values are data, so they live in one declarative place.
"""
from __future__ import annotations

from bot.models.items import EquipmentTemplate, EquipmentType, GachaCard, HuntEnemy, ItemRegistry
from bot.models.rarities import Rarity

# ---------------------------------------------------------------------------
# Gacha cards
# ---------------------------------------------------------------------------
_CARDS: tuple[tuple[str, str, Rarity, str, str], ...] = (
    # key,            name,            rarity,            series,        lore
    ("slime",         "Slimey",        Rarity.COMMON,     "Wilds",       "Just a blob trying its best."),
    ("rat",           "Sir Squeaks",   Rarity.COMMON,     "Wilds",       "Knight of the sewers."),
    ("mushroom",      "Shroomling",    Rarity.COMMON,     "Forest",      "Probably not edible."),
    ("bat",           "Echo",          Rarity.COMMON,     "Caves",       "Navigation by screaming."),
    ("wolf",          "Fenpup",        Rarity.UNCOMMON,   "Wilds",       "Loyal, bitey."),
    ("owl",           "Professor Hoot",Rarity.UNCOMMON,   "Forest",      "Tenure in Treeology."),
    ("golem",         "Pebblex",       Rarity.UNCOMMON,   "Mountains",   "Sturdy. Slow. Sincere."),
    ("kitsune",       "Kitsura",       Rarity.RARE,       "Spirits",     "Nine tails, zero patience."),
    ("knight",        "Ser Aldric",    Rarity.RARE,       "Kingdom",     "Sworn to the old code."),
    ("witch",         "Morgana",       Rarity.RARE,       "Coven",       "Reads stars and people."),
    ("reaper",        "Pale Harbinger",Rarity.EPIC,       "Nether",      "Delivers mail of the final kind."),
    ("dragonkin",     "Embyr",         Rarity.EPIC,       "Dragons",     "Small flame, big dreams."),
    ("celestial",     "Astra",         Rarity.LEGENDARY,  "Cosmos",      "Wove the night sky."),
    ("voidwalker",    "Nyxos",         Rarity.LEGENDARY,  "Nether",      "Steps between shadows."),
    ("worldserpent",  "Ouroxia",       Rarity.MYTHIC,     "Myth",        "Bites its tail; the world holds its breath."),
)

# ---------------------------------------------------------------------------
# Equipment templates
# ---------------------------------------------------------------------------
_EQUIPMENT: tuple[tuple[str, str, EquipmentType, int, int, int, Rarity], ...] = (
    # key,          name,             slot,             atk, def, luck, min_rarity
    ("rusty_sword", "Rusty Sword",    EquipmentType.WEAPON, 6,  0,  0, Rarity.COMMON),
    ("oak_club",    "Oak Club",       EquipmentType.WEAPON, 8,  1,  0, Rarity.COMMON),
    ("hunter_bow",  "Hunter's Bow",   EquipmentType.WEAPON, 10, 0,  2, Rarity.UNCOMMON),
    ("steel_blade", "Steel Blade",    EquipmentType.WEAPON, 14, 2,  1, Rarity.UNCOMMON),
    ("moon_saber",  "Moon Saber",     EquipmentType.WEAPON, 20, 3,  3, Rarity.RARE),
    ("ember_lance", "Ember Lance",    EquipmentType.WEAPON, 26, 4,  2, Rarity.RARE),
    ("void_reaver", "Void Reaver",    EquipmentType.WEAPON, 36, 5,  6, Rarity.EPIC),
    ("star_render", "Star Render",    EquipmentType.WEAPON, 50, 8,  9, Rarity.LEGENDARY),
    ("ouroboros",   "Ouroboros Edge", EquipmentType.WEAPON, 70, 10, 15, Rarity.MYTHIC),
    ("leather_vest","Leather Vest",   EquipmentType.ARMOR,  0,  5,  1, Rarity.COMMON),
    ("chain_mail",  "Chain Mail",     EquipmentType.ARMOR,  1,  9,  0, Rarity.UNCOMMON),
    ("dragon_scale","Dragon Scale",   EquipmentType.ARMOR,  2, 15,  2, Rarity.RARE),
    ("aegis_plate", "Aegis Plate",    EquipmentType.ARMOR,  3, 22,  4, Rarity.EPIC),
    ("aether_shell","Aether Shell",   EquipmentType.ARMOR,  5, 32,  7, Rarity.LEGENDARY),
    ("cosmic_mail", "Cosmic Mail",    EquipmentType.ARMOR,  7, 45, 10, Rarity.MYTHIC),
    ("lucky_coin",  "Lucky Charm",    EquipmentType.AMULET, 0,  1,  4, Rarity.COMMON),
    ("rabbit_foot", "Rabbit's Foot",  EquipmentType.AMULET, 1,  1,  7, Rarity.UNCOMMON),
    ("seer_orb",    "Seer's Orb",     EquipmentType.AMULET, 2,  3, 12, Rarity.RARE),
    ("phantom_band","Phantom Band",   EquipmentType.AMULET, 4,  5, 18, Rarity.EPIC),
    ("fate_ring",   "Ring of Fate",   EquipmentType.AMULET, 6,  8, 26, Rarity.LEGENDARY),
    ("infinity_eye","Eye of Infinity",EquipmentType.AMULET, 9, 10, 40, Rarity.MYTHIC),
)

# ---------------------------------------------------------------------------
# Hunt enemies
# ---------------------------------------------------------------------------
_ENEMIES: tuple[tuple[str, str, Rarity, int, int], ...] = (
    ("field_mouse", "Field Mouse",     Rarity.COMMON,     40,   12),
    ("thief",       "Roadside Thief",  Rarity.COMMON,     70,   15),
    ("boar",        "Tusker Boar",     Rarity.COMMON,     90,   18),
    ("bandit",      "Bandit Captain",  Rarity.UNCOMMON,   160,  28),
    ("golem",       "Stone Golem",     Rarity.UNCOMMON,   210,  34),
    ("wraith",      "Grave Wraith",    Rarity.RARE,       380,  55),
    ("hydra",       "Marsh Hydra",     Rarity.RARE,       450,  62),
    ("chimera",     "Ash Chimera",     Rarity.EPIC,       800,  110),
    ("elder_dragon","Elder Wyrm",      Rarity.LEGENDARY,  1_800, 260),
    ("avatar",      "Avatar of Ruin",  Rarity.MYTHIC,     4_200, 600),
)


def load_game_data() -> None:
    """Register all static content. Idempotent."""
    if len(ItemRegistry.cards):  # already loaded
        return
    for key, name, rarity, series, lore in _CARDS:
        ItemRegistry.cards[key] = GachaCard(key=key, name=name, rarity=rarity, lore=lore, series=series)
    for key, name, etype, atk, dfn, luck, min_r in _EQUIPMENT:
        ItemRegistry.equipment[key] = EquipmentTemplate(
            key=key, name=name, etype=etype,
            base_attack=atk, base_defense=dfn, base_luck=luck, min_rarity=min_r,
        )
    for key, name, rarity, coins, xp in _ENEMIES:
        ItemRegistry.enemies[key] = HuntEnemy(key=key, name=name, rarity=rarity, base_coins=coins, base_xp=xp)
