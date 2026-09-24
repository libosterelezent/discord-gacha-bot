"""Central configuration for the Gacha Bot.

Reads environment variables (12-factor style) and exposes typed,
frozen settings used across every layer of the application.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Immutable runtime configuration (frozen dataclass pattern)."""

    token: str
    command_prefix: str
    database_path: Path
    log_dir: Path
    log_level: str
    log_max_bytes: int
    log_backup_count: int

    # --- Game tuning knobs -------------------------------------------------
    starting_balance: int = 250
    daily_reward: int = 1_000
    daily_cooldown_hours: int = 24
    work_min: int = 150
    work_max: int = 500
    work_cooldown_seconds: int = 3_600

    gacha_pull_cost: int = 100
    gacha_multi_cost: int = 900
    gacha_pity_limit: int = 90          # guaranteed Legendary at 90 pulls
    gacha_soft_pity: int = 75           # Legendary odds ramp after this

    hunt_cooldown_seconds: int = 45
    huntbot_base_cost: int = 15_000
    huntbot_tick_seconds: int = 300     # one hunt cycle for the bot
    huntbot_battery_capacity: int = 24  # ticks stored while owner is away

    max_upgrade_level: int = 25

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Factory method assembling config from the environment."""
        return cls(
            token=os.getenv("DISCORD_TOKEN", ""),
            command_prefix=os.getenv("COMMAND_PREFIX", "!"),
            database_path=Path(os.getenv("DATABASE_PATH", _PROJECT_ROOT / "data" / "gacha.db")),
            log_dir=Path(os.getenv("LOG_DIR", _PROJECT_ROOT / "logs")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_max_bytes=_env_int("LOG_MAX_BYTES", 2_000_000),
            log_backup_count=_env_int("LOG_BACKUP_COUNT", 5),
        )


@dataclass(frozen=True, slots=True)
class GameConstants:
    """Pure game-design constants (data, not configuration)."""

    currency_name: str = "Coins"
    currency_emoji: str = "\U0001fa99"          # 🪙
    xp_per_hunt: tuple[int, ...] = (10, 40)
    xp_curve_base: float = 1.35                 # level n -> n+1 requirement
    shard_name: str = "Shards"
    shard_emoji: str = "\u2728"                 # ✨
    duplicate_shard_reward: dict[str, int] = field(
        default_factory=lambda: {"common": 1, "uncommon": 3, "rare": 8, "epic": 20, "legendary": 60, "mythic": 200}
    )


CONFIG: Final[BotConfig] = BotConfig.from_env()
CONSTANTS: Final[GameConstants] = GameConstants()
