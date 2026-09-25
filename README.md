# 🎰 Gacha Hunter — Discord Gacha Game Bot

A feature-complete Discord gacha game bot with a full player economy, a
gacha banner with a real pity system, equippable & forgeable gear, hunts
against rarity-tiered enemies, an automated **Huntbot** companion with
offline progress, and permanent player upgrades.

Built with **discord.py 2.x**, **aiosqlite** and Python 3.12+ (tested on
3.14). Every command works as both a **prefix command** (`!pull`) and a
**slash command** (`/pull`).

---

## ✨ Features

| System | What it does |
|---|---|
| 🪙 **Economy** | Coins currency: balance, daily rewards, hourly work, player-to-player transfers, 50/50 gambling, server leaderboard. Every coin mutation is written to an audit log table. |
| 🎲 **Gacha** | 6 rarity tiers (Common → Mythic) with weighted rolls, **soft pity** (Legendary odds ramp after pull 75), **hard pity** (guaranteed Legendary/Mythic at 90), duplicate pulls convert into ✨ **Shards** which buy discounted pulls, and a collectible card dex (15 unique cards across 5 series). |
| ⚔️ **Equipment** | Weapons / Armor / Amulets with rolled attack / defense / luck stats, 6 rarity tiers with stat multipliers, equipping changes your effective combat profile, forging up to **+10** with compounding stat growth, selling for coins. |
| 🎯 **Hunts** | Fight 10 enemy types from Field Mouse to Avatar of Ruin. Luck-tilted enemy rarity, power-based success rolls, coin/XP rewards, equipment & rare card drops, XP levels with escalating curve. |
| 🤖 **Huntbot** | Buy once (🪙 15,000), upgrade forever (+10% income/level). While active it banks coins and rare equipment every cycle into a **battery-capped** pool you must `collect`. Goes **offline-capable**: progress made while the bot was down is reconciled from wall-clock time on boot. |
| 🔧 **Upgrades** | 5 permanent perks with exponential cost curves: **Fortune** (luck), **Greed** (+5% coins/level), **Swiftness** (-2% cooldowns/level), **Battle Power**, **Harvest** (+4% huntbot yield/level). |
| 📖 **Help menu** | Interactive **Components V2** menu (`!help`): gold Container with bot avatar thumbnail, a category **select menu** that swaps pages in-place, and a Home button. |
| 📜 **Logging & safety** | Rotating file logs, per-command timing, sliding-window rate limiters, domain exception hierarchy mapped to friendly embeds, transaction rollback on any error. |

---

## 📖 Command List

Prefix defaults to `!` (configurable). All commands also work as slash commands.

### 🪙 Economy
| Command | Aliases | Description |
|---|---|---|
| `!balance` | `bal`, `wallet` | Show your coin balance and shards |
| `!daily` | — | Claim your daily reward (24h cooldown, scales with level) |
| `!work` | — | Earn coins (hourly, boosted by Greed) |
| `!pay <@user> <amount>` | `give` | Send coins to another player |
| `!gamble <amount>` | — | 50/50 double-or-nothing |
| `!leaderboard` | `lb`, `top` | Top 10 richest hunters |

### 🎲 Gacha
| Command | Aliases | Description |
|---|---|---|
| `!pull [1\|10]` | `gacha`, `wish` | Single pull 🪙100 or 10-pull 🪙900 (10% discount) |
| `!collection [@user]` | `coll`, `dex` | View a card collection & dex progress |
| `!sell_dupes` | — | Convert duplicate cards into coins |
| `!shards` | — | Shard balance & info |
| `!shards pull` | — | Spend shards on a pull (10 shards) |

### 🎯 Hunts & Profile
| Command | Aliases | Description |
|---|---|---|
| `!hunt` | — | Hunt a random enemy for coins, XP and loot (45s cooldown) |
| `!profile [@user]` | `me`, `stats` | Full profile: stats, gear, pity, huntbot |
| `!hunt_info` | — | How hunting, luck and cooldowns work |

### 🤖 Huntbot
| Command | Description |
|---|---|
| `!huntbot` / `!huntbot info` | Status: level, battery, banked rewards, next upgrade price |
| `!huntbot buy` | Purchase your bot (activates it immediately) |
| `!huntbot upgrade` | +1 level: more income & efficiency |
| `!huntbot toggle` | Start / stop automatic hunting |
| `!huntbot collect` | Sweep banked coins & items (resets battery) |

### ⚔️ Equipment
| Command | Description |
|---|---|
| `!equipment` | List all gear (📮 = equipped) |
| `!equip <id>` | Equip a piece (per-slot: weapon / armor / amulet) |
| `!unequip <id>` | Unequip |
| `!upgequip <id>` | Forge +1 level (compounding stats, up to +10) |
| `!sellgear <id>` | Sell a piece for coins |

### 🔧 Upgrades
| Command | Description |
|---|---|
| `!upgrades` | Workshop: levels, effects, next-level costs |
| `!upgrades buy <name>` | Buy one level, e.g. `!upgrades buy greed` |

### 📖 Help
| Command | Description |
|---|---|
| `help` | Interactive Components V2 menu — pick a category from the dropdown |

### 🛡️ Admin (owner only)
| Command | Description |
|---|---|
| `!grant <@user> <amount>` | Mint coins for a player |
| `!botstats` | Players, pulls, circulation, uptime, versions |

---

## 🚀 Installation

### 1. Create the Discord application
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. Open the **Bot** tab → **Reset Token** → copy the token.
3. Under **Privileged Gateway Intents**, enable:
   - ✅ **Server Members Intent**
   - ✅ **Message Content Intent**
4. Invite the bot via **OAuth2 → URL Generator** with scopes `bot` +
   `applications.commands` and *Send Messages* / *Embed Links*
   permissions, then open the generated URL.

### 2. Run the bot

**Requirements:** Python 3.12+ and pip.

```bash
git clone https://github.com/libosterelezent/discord-gacha-bot.git
cd discord-gacha-bot
pip install -r requirements.txt

cp .env.example .env      # Windows: copy .env.example .env
```

Edit `.env`:

```ini
DISCORD_TOKEN=your-token-here
COMMAND_PREFIX=!
# Optional:
# DATABASE_PATH=/path/to/gacha.db
# LOG_LEVEL=INFO          # DEBUG | INFO | WARNING | ERROR
```

Start it:

```bash
python main.py
```

Logs are written to `logs/gacha-bot.log` (rotating, 2 MB × 5 backups).
The SQLite database is created automatically at `data/gacha.db` on first
run — no migration steps needed.

---

## 🧠 Game Tips

- Pull until you hit **pity 90** for a guaranteed Legendary — pity never
  resets except on the guaranteed hit.
- Equip gear before hunting: **power** decides win rate, **luck** tilts
  enemy & loot rarity, and every **level** adds +1% coins.
- Huntbots only charge while *active* and stop when the battery (24
  cycles) is full — **collect regularly** or you lose throughput.
- `Harvest` upgrade applies to huntbot income multiplicatively with bot
  level efficiency — stack both for passive wealth.

---

## 🏗️ Architecture

```
main.py                     entry point (logging, lifecycle)
bot/
├── config.py               frozen dataclass settings loaded from .env
├── core/
│   ├── database.py         aiosqlite facade, async transactions, migrations
│   ├── gacha_bot.py        Bot subclass: service graph, global error handlers
│   ├── logging_setup.py    rotating file + console logging
│   ├── decorators.py       timing, rate limiting, error translation
│   └── exceptions.py       domain exception hierarchy
├── models/                 enums, dataclasses, item registries, game content
├── services/               all game logic (economy, gacha, hunt, huntbot…)
├── cogs/                   thin Discord adapters (hybrid commands)
└── utils/                  embed helpers
```

**Advanced Python concepts used:** `asyncio` background task with graceful
cancellation · async context-manager transactions with auto
commit/rollback · `asyncio.Lock` write serialisation + WAL mode · ABC
service layer with dependency injection · frozen / slots dataclasses ·
behavioural `IntEnum` · metaclass item registry · `ClassVar` · properties
· factory & classmethods · sliding-window rate limiter decorator ·
exception hierarchy mapped to user-facing embeds · versioned schema
migrations · rotating log handlers with a custom formatter.

## 📄 License

MIT — do whatever you want, attribution appreciated.
