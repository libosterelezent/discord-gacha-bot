"""Central configuration for the Gacha Bot.

Two layers, two owners:

* :class:`BotConfig` — infrastructure settings from environment
  variables (12-factor style). Owned by the host.
* :class:`GameSettings` — game-design tuning knobs loaded from
  ``config/game.json``. Owned by the *maintainer*: cooldowns, costs,
  pity parameters and spawn behaviour can be retuned without touching
  code, and reloaded at runtime via ``!reload_settings``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, make_dataclass
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("gacha.config")

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_GAME_CONFIG_PATH: Final[Path] = _PROJECT_ROOT / "config" / "game.json"


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Immutable infrastructure configuration."""

    token: str
    command_prefix: str
    database_url: str
    default_sqlite_path: Path
    log_dir: Path
    log_level: str
    log_max_bytes: int
    log_backup_count: int
    maintainer_guild_id: int | None

    @classmethod
    def from_env(cls) -> "BotConfig":
        raw_guild = os.getenv("MAINTAINER_GUILD_ID", "").strip()
        sqlite_path = _PROJECT_ROOT / "data" / "game.db"
        return cls(
            token=os.getenv("DISCORD_TOKEN", ""),
            command_prefix=os.getenv("COMMAND_PREFIX", "!"),
            database_url=os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{sqlite_path}"),
            default_sqlite_path=sqlite_path,
            log_dir=Path(os.getenv("LOG_DIR", _PROJECT_ROOT / "logs")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_max_bytes=_env_int("LOG_MAX_BYTES", 2_000_000),
            log_backup_count=_env_int("LOG_BACKUP_COUNT", 5),
            maintainer_guild_id=int(raw_guild) if raw_guild.isdigit() else None,
        )


# ---------------------------------------------------------------------------
# Game settings (maintainer-editable JSON)
# ---------------------------------------------------------------------------

def _nested_dataclass(name: str, payload: dict[str, Any]) -> type:
    """Build a nested dataclass tree from a plain JSON dict.

    Missing keys fall back to the defaults declared in ``_DEFAULTS`` so a
    partially-filled game.json still boots.
    """
    annotations: dict[str, Any] = {}
    values: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            nested_cls = _nested_dataclass(f"{name}_{key}", value)
            annotations[key] = nested_cls
            values[key] = nested_cls()
        else:
            annotations[key] = type(value)
            values[key] = value
    cls = make_dataclass(
        name,
        [(k, ann, field(default=values[k])) for k, ann in annotations.items()],
        frozen=True,
        slots=True,
    )
    return cls


class GameSettings:
    """Typed, reloadable view over ``config/game.json``.

    Nested sections are exposed as attributes, e.g.
    ``SETTINGS.gacha.pity_limit``. Unknown sections are kept as-is under
    their own attribute so future JSON additions never crash old code.
    """

    def __init__(self, path: Path = _GAME_CONFIG_PATH) -> None:
        self._path = path
        self._apply(self._load())

    # -- lifecycle -----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            logger.warning("game.json not found at %s — using defaults", self._path)
            return json.loads(_DEFAULT_GAME_JSON)
        with self._path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return _merge(json.loads(_DEFAULT_GAME_JSON), data)

    def _apply(self, data: dict[str, Any]) -> None:
        for section, value in data.items():
            if isinstance(value, dict):
                setattr(self, section, _nested_dataclass(section, value)())
            else:
                setattr(self, section, value)

    def reload(self) -> None:
        """Re-read game.json and swap every section in place."""
        self._apply(self._load())
        logger.info("Game settings reloaded from %s", self._path)

    # -- helpers -------------------------------------------------------------

    def money(self, amount: int) -> str:
        return f"{self.currency.emoji} **{amount:,}**"

    def shard(self, amount: int) -> str:
        return f"{self.shards.emoji} **{amount:,}**"

    def cooldown_seconds(self, action: str) -> float:
        return float(getattr(self.cooldowns.fixed, action, 0.0))

    def window(self, action: str) -> tuple[int, float]:
        spec = getattr(self.cooldowns.windows, action, None)
        if spec is None:
            return (1, 0.0)
        return (int(spec.max_calls), float(spec.window))


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge JSON objects: override wins, missing keys keep defaults."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


_DEFAULT_GAME_JSON: Final[str] = json.dumps(
    {
        "economy": {
            "scope": "guild",
            "starting_balance": 250,
            "daily_reward": 1000,
            "daily_level_bonus": 25,
            "work_min": 150,
            "work_max": 500,
        },
        "currency": {"name": "Coins", "emoji": "\U0001fa99"},
        "shards": {"name": "Shards", "emoji": "\u2728"},
        "xp": {"per_hunt_min": 10, "per_hunt_max": 40, "curve_base": 1.35},
        "cooldowns": {
            "fixed": {"daily": 86400, "work": 3600, "hunt": 45},
            "windows": {"pull": {"max_calls": 3, "window": 10}},
        },
        "spawn": {"algorithm": "weighted_luck", "luck_tilt_exponent": 1.0},
        "gacha": {
            "pull_cost": 100,
            "multi_cost": 900,
            "multi_count": 10,
            "shard_pull_divisor": 10,
            "pity_limit": 90,
            "soft_pity": 75,
            "soft_ramp": 8.0,
            "pity_floor": "legendary",
            "pity_floor_upgrade_chance": 0.9,
            "card_chance": 0.6,
        },
        "hunt": {
            "tier_cap_divisor": 60,
            "equipment_drop_base": 0.06,
            "equipment_drop_per_tier": 0.02,
            "equipment_drop_luck_scale": 0.15,
            "card_drop_per_tier": 0.015,
            "card_drop_luck_scale": 0.05,
        },
        "huntbot": {
            "base_cost": 15000,
            "cost_growth": 1.6,
            "tick_seconds": 300,
            "battery_capacity": 24,
            "loop_interval": 60,
            "item_drop_chance": 0.08,
            "base_income": 35,
            "income_per_level": 15,
            "efficiency_per_level": 0.1,
        },
        "equipment": {
            "max_level": 10,
            "forge_growth": 0.12,
            "upgrade_cost_base": 250,
            "sell_level_bonus": 0.15,
        },
        "upgrades": {"max_level": 25},
    }
)

CONFIG: Final[BotConfig] = BotConfig.from_env()
SETTINGS: Final[GameSettings] = GameSettings()
