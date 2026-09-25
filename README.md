# 🎰 Gacha Hunter — Discord Gacha Game Bot

A modular Discord gacha game bot with a full player economy, a gacha
banner with a real pity system, equippable & forgeable gear, hunts
against rarity-tiered enemies, an automated **Huntbot** companion with
offline progress, permanent upgrades, **guild-scoped gameplay** with
global badges, and a maintainer **event-logging** system.

Built with **discord.py 2.6+ (Components V2)**, **SQLAlchemy 2 async**
(PostgreSQL or SQLite — same code) and Python 3.12+. Commands work
both as slash commands and as text commands using your server's
configured prefix (see `.env` → `COMMAND_PREFIX`).

---

## ✨ Architecture

The bot is split into independent layers so **adding content or
features never touches the command interface**:

| Layer | Location | Owns |
|---|---|---|
| 🧩 **Content** | `bot/content/data/*.json` | Cards, equipment, enemies, upgrades, badges and the **rarity table (incl. spawn weights)** — pure data, edit JSON to extend the game |
| ⚙️ **Systems** | `bot/services/` | All game logic (economy, gacha, hunt, huntbot, equipment, upgrades, badges) — content-agnostic and Discord-free |
| 🖥️ **Interface** | `bot/cogs/` + `bot/ui/` | Thin command adapters + Components V2 presentation builders; the **theme** (colours, footers, titles) lives in one place |
| 🔎 **Observability** | `bot/observability/` | Routes domain events to Discord channels you pick, per category |
| 🗄️ **Persistence** | `bot/core/database.py` | SQLAlchemy async engine — `DATABASE_URL` picks PostgreSQL or SQLite; versioned migrations included |

**Extending the game = adding JSON.** A new card, weapon, enemy,
badge, rarity tier (with its own spawn weight/colour/rewards) or
upgrade is a new entry in `bot/content/data/`. Spawn behaviour is a
pluggable algorithm selected in `config/game.json`
(`spawn.algorithm`); new algorithms register with one decorator.

### Maintainer tuning — `config/game.json`

Every cooldown, cost curve, pity parameter, drop rate and currency
name is maintainer-editable there, and **hot-reloadable** with the
`reload_settings` command — no restart, no code changes.

### Guild vs global scope

By default the economy is **guild-scoped**: each server has its own
balances, leaderboards and cooldowns (no cross-server conflicts), while
a global identity table powers cross-server badges and global
rankings (`leaderboard global`). Flip `economy.scope` to `"global"`
in `config/game.json` for one shared economy across all servers — the
schema supports both modes natively.

### Rate limits & cooldowns

* discord.py tracks Discord's per-route rate-limit buckets from the
  `X-RateLimit-*` headers and pre-emptively waits — the bot never
  hammers an endpoint into a 429.
* The event-logging sink sends through a semaphore-capped queue with
  explicit `RetryAfter` back-off and jitter; failed channel sends are
  dropped-and-logged so logging can never stall gameplay.
* All gameplay cooldowns (daily/work/hunt fixed timers, pull sliding
  window) live in `config/game.json` and are scope-aware.

---

## 📖 Command List

Commands work as slash commands everywhere, and as text commands with
your configured prefix (set `COMMAND_PREFIX` in `.env`). The default
prefix is configurable by the maintainer.

### 🪙 Economy
| Command | Aliases | Description |
|---|---|---|
| `balance` | `bal`, `wallet` | Show your coin balance and shards |
| `daily` | — | Claim your daily reward (24h cooldown, scales with level) |
| `work` | — | Earn coins (hourly, boosted by Greed) |
| `pay <@user> <amount>` | `give` | Send coins to another player |
| `gamble <amount>` | — | 50/50 double-or-nothing |
| `leaderboard [guild\|global]` | `lb`, `top` | Top 10 richest hunters |

### 🎲 Gacha
| Command | Aliases | Description |
|---|---|---|
| `pull [1\|10]` | `gacha`, `wish` | Single pull 🪙100 or 10-pull 🪙900 (10% discount) |
| `collection [@user]` | `coll`, `dex` | View a card collection & dex progress |
| `sell_dupes` | — | Convert duplicate cards into coins |
| `shards` | — | Shard balance & info |
| `shards pull` | — | Spend shards on a pull (10 shards) |

### 🎯 Hunts & Profile
| Command | Aliases | Description |
|---|---|---|
| `hunt` | — | Hunt a random enemy for coins, XP and loot (45s cooldown) |
| `profile [@user]` | `me`, `stats` | Full profile: stats, gear, pity, huntbot, badges |
| `hunt_info` | — | How hunting, luck and cooldowns work |

### 🤖 Huntbot
| Command | Description |
|---|---|
| `huntbot` / `huntbot info` | Status: level, battery, banked rewards, next upgrade price |
| `huntbot buy` | Purchase your bot (activates it immediately) |
| `huntbot upgrade` | +1 level: more income & efficiency |
| `huntbot toggle` | Start / stop automatic hunting |
| `huntbot collect` | Sweep banked coins & items (resets battery) |

### ⚔️ Equipment
| Command | Description |
|---|---|
| `equipment` | List all gear (📮 = equipped) |
| `equip <id>` | Equip a piece (per-slot: weapon / armor / amulet) |
| `unequip <id>` | Unequip |
| `upgequip <id>` | Forge +1 level (compounding stats, up to +10) |
| `sellgear <id>` | Sell a piece for coins |

### 🔧 Upgrades
| Command | Description |
|---|---|
| `upgrades` | Workshop: levels, effects, next-level costs |
| `upgrades buy <name>` | Buy one level, e.g. `upgrades buy greed` |

### 🛡️ Admin (owner only)
| Command | Description |
|---|---|
| `grant <@user> <amount>` | Mint coins for a player |
| `badges [@user]` | List badge definitions, or a player's badges |
| `badges grant <@user> <key>` | Award a badge (guild or global scope) |
| `badges revoke <@user> <key>` | Remove a badge |
| `logchannel set <category> #channel` | Route an event category to a channel |
| `logchannel remove <category>` | Stop logging a category |
| `logchannel list` | Show configured logging channels |
| `reload_settings` | Hot-reload `config/game.json` |
| `botstats` | Players, pulls, circulation, uptime, versions |

**Log categories:** `economy`, `gacha`, `hunt`, `huntbot`,
`equipment`, `upgrades`, `admin`, `error`, and `all` (wildcard).
Events are best-effort: a channel without permissions is skipped, and
setting `MAINTAINER_GUILD_ID` in `.env` mirrors **error** events to
that guild's configured error channel from every server the bot is in.

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
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # Windows: copy .env.example .env
```

Edit `.env`:

```ini
DISCORD_TOKEN=your-token-here
COMMAND_PREFIX=<your-prefix>
# SQLite for dev (zero setup):
DATABASE_URL=sqlite+aiosqlite:///data/game.db
# PostgreSQL for production:
# DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/gacha
# Optional:
# MAINTAINER_GUILD_ID=123...   # guild that receives error logs centrally
# LOG_LEVEL=INFO
```

Start it:

```bash
python main.py
```

Logs are written to `logs/gacha-bot.log` (rotating, 2 MB × 5 backups).
The database schema is created automatically on first run — no
migration steps needed.

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

## 🏗️ Project layout

```
main.py                     entry point (logging, lifecycle)
config/game.json            maintainer tuning (cooldowns, costs, pity, spawn)
bot/
├── config.py               env config + typed game-settings loader (hot-reloadable)
├── content/                JSON game content + registry + spawn algorithms
│   └── data/               rarities / cards / equipment / enemies / upgrades / badges
├── core/
│   ├── database.py         SQLAlchemy async facade, guild-scoped schema, migrations
│   ├── gacha_bot.py        Bot subclass: service graph, event bus, error handlers
│   ├── events.py           domain event bus (pub/sub with isolation)
│   ├── cooldowns.py        maintainer-configurable, scope-aware cooldowns
│   ├── ratelimit.py        RetryAfter-aware outbound limiter
│   ├── logging_setup.py    rotating file + console logging
│   ├── decorators.py       timing, error translation, safe-run helpers
│   └── exceptions.py       domain exception hierarchy
├── models/                 player, stats, items (rarity = content data)
├── services/               game logic: economy, gacha, hunt, huntbot,
│                           equipment, upgrades, badges
├── ui/                     theme (colours/footers), Components V2 builders,
│                           interactive help (dropdown + buttons)
├── observability/          Discord channel logging sink
└── cogs/                   thin Discord adapters (hybrid commands)
```

**Advanced Python concepts used:** async SQLAlchemy with
dialect-portable SQL and `RETURNING` inserts · async context-manager
transactions with auto commit/rollback · pub/sub event bus with
handler isolation · frozen / slots dataclasses · data-driven content
registries with pluggable strategy algorithms · metaclass-free typed
settings generated from JSON · sliding-window + fixed cooldown
manager · Components V2 layout views · graceful global error handlers
(prefix, slash and event layers).

## 📄 License

MIT — do whatever you want, attribution appreciated.
