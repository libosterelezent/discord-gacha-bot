"""Scenario battery for the local simulator (tools/simulator.py).

Runs the real command pipeline for two fake users (player + owner),
asserts on both the captured replies and database state, then restarts
the bot on the same database to prove persistence.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# DATABASE_URL must be pinned BEFORE bot.config imports (CONFIG is read at
# import time) so each simulated run gets an isolated database.
_TMP = tempfile.mkdtemp(prefix="gacha-sim-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(_TMP) / 'sim.db'}"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulator import (  # noqa: E402
    GUILD_ID, OWNER_ID, PLAYER_ID, Simulator,
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name} {detail}")


def expect_reply_text(sim: "Simulator", needle: str) -> bool:
    return needle.lower() in sim.http.last_text().lower()


async def main() -> int:
    db_url = os.environ["DATABASE_URL"]
    print(f"booting simulated bot (db={db_url}) ...")
    sim = Simulator()
    await sim.boot()
    bot = sim.bot
    assert bot is not None
    print(f"  booted: {len(bot.cogs)} cogs, {len(bot.tree.get_commands())} slash commands\n")

    # -- lifecycle & basics ---------------------------------------------------
    print("[basics]")
    await sim.send(PLAYER_ID, "player", "!balance")
    check("balance shows starting wallet", expect_reply_text(sim, "player"))

    await sim.send(PLAYER_ID, "player", "!daily")
    check("daily claimed", expect_reply_text(sim, "daily reward claimed"))

    await sim.send(PLAYER_ID, "player", "!daily")
    check("daily cooldown blocks re-claim", expect_reply_text(sim, "slow down"))

    await sim.send(PLAYER_ID, "player", "!work")
    check("work pays", "worked" in sim.http.last_text().lower())

    rows = await sim.db.fetch_all(
        "SELECT delta, reason FROM economy_log WHERE user_id = :u ORDER BY id",
        {"u": PLAYER_ID},
    )
    reasons = {r["reason"] for r in rows}
    check("economy audit trail written", {"daily", "work"} <= reasons, f"got {reasons}")

    await sim.send(PLAYER_ID, "player", "!gamble 999999")
    check("gamble over-balance rejected", expect_reply_text(sim, "need"))

    # -- gacha ------------------------------------------------------------------
    print("[gacha]")
    await sim.send(OWNER_ID, "owner", f"!grant <@{PLAYER_ID}> 50000")
    check("owner grant works", expect_reply_text(sim, "granted"))

    await sim.send(PLAYER_ID, "player", "!pull")
    check("pull replies with results", expect_reply_text(sim, "pull results"))

    await sim.send(PLAYER_ID, "player", "!pull 10")
    check("multi pull works", "pull results x10" in sim.http.last_text().lower())

    await sim.send(PLAYER_ID, "player", "!collection")
    check("collection renders", expect_reply_text(sim, "collection"))

    await sim.send(PLAYER_ID, "player", "!shards")
    check("shards info renders", expect_reply_text(sim, "shards"))

    # -- hunt -------------------------------------------------------------------
    print("[hunt]")
    await sim.send(PLAYER_ID, "player", "!hunt")
    text = sim.http.last_text().lower()
    check("hunt resolves", "the hunt" in text, text[:100])

    await sim.send(PLAYER_ID, "player", "!hunt")
    check("hunt cooldown applies", expect_reply_text(sim, "slow down"))

    await sim.send(PLAYER_ID, "player", "!profile")
    check("profile renders", expect_reply_text(sim, "level"))

    # -- equipment / upgrades ----------------------------------------------------
    print("[equipment]")
    await sim.send(PLAYER_ID, "player", "!equipment")
    equip_text = sim.http.last_text()
    if "no equipment" not in equip_text.lower():
        check("equipment lists pieces", True)
        import re
        m = re.search(r"#(\d+)", equip_text)
        if m:
            eid = m.group(1)
            await sim.send(PLAYER_ID, "player", f"!equip {eid}")
            check("equip by id", expect_reply_text(sim, "equipped"))
            await sim.send(PLAYER_ID, "player", f"!upgequip {eid}")
            check("forge upgrade", "forged" in sim.http.last_text().lower())
    else:
        check("equipment empty state handled", True)

    print("[upgrades]")
    await sim.send(PLAYER_ID, "player", "!upgrades")
    check("upgrades catalog renders", expect_reply_text(sim, "workshop"))
    await sim.send(PLAYER_ID, "player", "!upgrades buy luck")
    check("buy upgrade by key", "luck" in sim.http.last_text().lower())
    await sim.send(PLAYER_ID, "player", "!upgrades buy nonsense")
    check("unknown upgrade rejected", expect_reply_text(sim, "unknown upgrade"))

    # -- huntbot -----------------------------------------------------------------
    print("[huntbot]")
    await sim.send(PLAYER_ID, "player", "!huntbot buy")
    check("huntbot purchase", expect_reply_text(sim, "huntbot acquired"))

    await sim.send(PLAYER_ID, "player", "!huntbot")
    check("huntbot status", expect_reply_text(sim, "huntbot"))

    await sim.send(PLAYER_ID, "player", "!huntbot toggle")
    check("huntbot toggle", "stopped" in sim.http.last_text().lower() or "started" in sim.http.last_text().lower())

    await sim.send(PLAYER_ID, "player", "!huntbot toggle")
    await sim.send(PLAYER_ID, "player", "!huntbot collect")
    check("collect handles empty battery", expect_reply_text(sim, "collect") or expect_reply_text(sim, "nothing"))

    # force ticks via the service, then collect
    await sim.huntbot_tick()
    await sim.send(PLAYER_ID, "player", "!huntbot collect")
    check("collect after tick", "collected" in sim.http.last_text().lower())

    # -- help & admin gating ------------------------------------------------------
    print("[help & admin]")
    await sim.send(PLAYER_ID, "player", "!help")
    check("help menu sends Components V2 payload",
          any(m.components for m in sim.http.sent[-1:]),
          "no components in last message")

    await sim.send(PLAYER_ID, "player", "!grant <@{PLAYER_ID}> 100")
    check("non-owner cannot grant", expect_reply_text(sim, "permission"))

    await sim.send(PLAYER_ID, "player", "!botstats")
    check("non-owner cannot botstats", expect_reply_text(sim, "permission"))

    await sim.send(OWNER_ID, "owner", "!botstats")
    check("owner botstats", expect_reply_text(sim, "bot statistics"))

    await sim.send(OWNER_ID, "owner", "!reload_settings")
    check("reload_settings", expect_reply_text(sim, "settings reloaded"))

    await sim.send(OWNER_ID, "owner", "!reload_content")
    check("reload_content", expect_reply_text(sim, "content reloaded"))

    await sim.send(OWNER_ID, "owner", f"!player <@{PLAYER_ID}>")
    check("player data inspection", expect_reply_text(sim, "player data"))

    await sim.send(OWNER_ID, "owner", f"!player setcoins <@{PLAYER_ID}> 12345")
    check("player setcoins", expect_reply_text(sim, "balance updated"))
    bal = await sim.fetch_val(
        "SELECT balance FROM players WHERE guild_id = :g AND user_id = :u",
        {"g": GUILD_ID, "u": PLAYER_ID},
    )
    check("setcoins persisted", bal == 12345, f"bal={bal}")

    # -- transfers -----------------------------------------------------------------
    print("[transfers]")
    await sim.send(OWNER_ID, "owner", "!daily")
    await sim.send(PLAYER_ID, "player", f"!pay <@{OWNER_ID}> 500")
    check("pay works", expect_reply_text(sim, "500"))
    bal_after_pay = await sim.fetch_val(
        "SELECT balance FROM players WHERE guild_id = :g AND user_id = :u",
        {"g": GUILD_ID, "u": PLAYER_ID},
    )
    check("player balance after pay", bal_after_pay == 11_845, f"bal={bal_after_pay}")

    # -- leaderboard ----------------------------------------------------------------
    await sim.send(PLAYER_ID, "player", "!leaderboard")
    check("leaderboard renders", expect_reply_text(sim, "wealthiest"))

    # -- restart persistence ---------------------------------------------------------
    print("[restart]")
    daily_left = await sim.fetch_val(
        "SELECT COUNT(*) FROM cooldowns WHERE action = 'daily' AND user_id = :u",
        {"u": PLAYER_ID},
    )
    check("daily cooldown row persisted before restart", int(daily_left or 0) == 1)

    await sim.shutdown()
    sim2 = Simulator()
    await sim2.boot()
    await sim2.send(PLAYER_ID, "player", "!daily")
    check("daily still cooling after restart", "slow down" in sim2.http.last_text().lower(),
          sim2.http.last_text()[:80])

    bal2 = await sim2.fetch_val(
        "SELECT balance FROM players WHERE guild_id = :g AND user_id = :u",
        {"g": GUILD_ID, "u": PLAYER_ID},
    )
    check("balance survived restart", bal2 == 11_845, f"bal={bal2}")

    players = await sim2.fetch_val("SELECT COUNT(*) FROM players")
    check("player rows sane", int(players or 0) == 2, f"players={players}")

    await sim2.shutdown()

    print(f"\n{'=' * 60}\nRESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", *FAILED, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
