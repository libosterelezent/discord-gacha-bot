"""Adversarial command battery: intended use, edge cases, and abuse.

Every command is exercised the way a well-behaved user would use it,
the way a careless user might (typos, zero/negative/huge numbers, wrong
types), and the way a hostile user would (races, cross-path cooldown
bypass, IDOR, oversized payloads, DM-context probing).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# DATABASE_URL must be pinned BEFORE bot.config imports (isolated per-run DB)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(tempfile.mkdtemp(prefix='gacha-abuse-')) / 'abuse.db'}"
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator import GUILD_ID, OWNER_ID, PLAYER_ID, Simulator  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name} {detail}")


def bot_settings_gacha_pity() -> int:
    from bot.config import SETTINGS

    return SETTINGS.gacha.pity_limit


def _find_custom_id(node, component_type: int):
    if isinstance(node, dict):
        if node.get("type") == component_type and "custom_id" in node:
            yield node["custom_id"]
        for child in node.get("components", []):
            yield from _find_custom_id(child, component_type)
    elif isinstance(node, list):
        for child in node:
            yield from _find_custom_id(child, component_type)


def has(sim, needle: str) -> bool:
    return needle.lower() in sim.http.last_text().lower()


async def audit_count(sim: "Simulator", reason_prefix: str, user_id: int) -> int:
    val = await sim.fetch_val(
        "SELECT COUNT(*) FROM economy_log WHERE user_id = :u AND reason LIKE :r",
        {"u": user_id, "r": f"{reason_prefix}%"},
    )
    return int(val or 0)


async def main() -> int:
    print(f"booting adversarial battery (db={os.environ['DATABASE_URL']}) ...")
    sim = Simulator()
    await sim.boot()
    bot = sim.bot
    assert bot is not None
    bot.content._encounter_chance = 0.0  # deterministic; encounter tested separately
    print(f"  booted: {len(bot.cogs)} cogs\n")

    VICTIM = 900_000_000_000_000_777  # second player: fresh, broke
    await sim.send(VICTIM, "victim", "!balance")  # create row
    await sim.send(OWNER_ID, "owner", f"!grant <@{VICTIM}> 30000")

    # ------------------------------------------------------------------ economy
    print("[economy: intended + careless]")
    await sim.send(PLAYER_ID, "player", "!daily")
    check("daily intended", has(sim, "daily reward"))

    await sim.send(PLAYER_ID, "player", "!DAILY")
    check("uppercase alias cooldown-blocked", has(sim, "slow down"))
    await sim.slash(PLAYER_ID, "daily")
    check("slash after prefix daily blocked", "slow down" in sim.http.last_text().lower())

    await sim.send(PLAYER_ID, "player", "!gamble 0")
    check("gamble zero rejected", has(sim, "int") or has(sim, "range") or has(sim, "error"))
    await sim.send(PLAYER_ID, "player", "!gamble -5")
    check("gamble negative rejected", not has(sim, "won"))
    await sim.send(PLAYER_ID, "player", "!gamble abc")
    check("gamble non-numeric rejected", not has(sim, "won"))
    await sim.send(PLAYER_ID, "player", "!gamble 10.5")
    check("gamble float rejected", not has(sim, "won"))

    print("[economy: hostile]")
    fresh = 900_000_000_000_000_888
    await sim.send(fresh, "fresh", "!balance")
    await sim.send_nowait(fresh, "fresh", "!daily")
    await sim.send_nowait(fresh, "fresh", "!daily")
    await sim.drain()
    n = await audit_count(sim, "daily", fresh)
    check("concurrent daily credits exactly once", n == 1, f"audit={n}")

    await sim.send_nowait(fresh, "fresh", "!work")
    await sim.send_nowait(fresh, "fresh", "!work")
    await sim.drain()
    n = await audit_count(sim, "work", fresh)
    check("concurrent work credits exactly once", n == 1, f"audit={n}")

    await sim.send(OWNER_ID, "owner", f"!grant <@{fresh}> 250")
    await sim.send_nowait(fresh, "fresh", "!gamble 250")
    await sim.send_nowait(fresh, "fresh", "!gamble 250")
    await sim.drain()
    bal = await sim.fetch_val(
        "SELECT balance FROM players WHERE user_id = :u AND guild_id = :g",
        {"u": fresh, "g": GUILD_ID},
    )
    check("concurrent gamble cannot go negative", bal is not None and bal >= 0, f"bal={bal}")

    await sim.send(OWNER_ID, "owner", f"!player setcoins <@{fresh}> 0")
    await sim.send(fresh, "fresh", f"!pay <@{fresh}> 100")
    check("cannot pay yourself", has(sim, "yourself"))
    await sim.send(fresh, "fresh", f"!pay <@{PLAYER_ID}> 999999")
    check("over-balance pay rejected", has(sim, "need"))
    await sim.send(fresh, "fresh", "!leaderboard garbage")
    check("bad leaderboard scope falls back", has(sim, "wealthiest"))
    await sim.send(fresh, "fresh", "!leaderboard GLOBAL")
    check("uppercase scope accepted", has(sim, "wealthiest"))

    await sim.send(OWNER_ID, "owner", f"!player setcoins <@{fresh}> 9999999999999999999999999999")
    check("oversized setcoins refused cleanly",
          has(sim, "too large") or has(sim, "error"), sim.http.last_text()[:60])
    await sim.send(fresh, "fresh", "!gamble 9999999999999999999999999999")
    check("oversized gamble refused cleanly",
          has(sim, "too large") or has(sim, "need"), sim.http.last_text()[:60])

    # ------------------------------------------------------------------ gacha
    print("[gacha]")
    await sim.send(fresh, "fresh", "!pull 0")
    check("pull zero rejected", has(sim, "single pull"))
    await sim.send(fresh, "fresh", "!pull -1")
    check("pull negative rejected", has(sim, "single pull"))
    await sim.send(fresh, "fresh", "!pull 2")
    check("pull odd count rejected", has(sim, "single pull"))
    await sim.send(fresh, "fresh", "!pull 10")
    check("multi pull broke rejected", has(sim, "need"))
    await sim.send(fresh, "fresh", "!shards pull")
    check("shards pull without shards rejected", has(sim, "need"))
    await sim.send(fresh, "fresh", "!sell_dupes")
    check("sell_dupes empty handled", has(sim, "no duplicate"))
    await sim.send(fresh, "fresh", f"!collection <@{VICTIM}>")
    check("viewing someone else's collection", has(sim, "collection"))

    funded = 900_000_000_000_000_999
    await sim.send(funded, "funded", "!balance")
    await sim.send(OWNER_ID, "owner", f"!grant <@{funded}> 10000")
    for _ in range(3):
        await sim.send_nowait(funded, "funded", "!pull")
    await sim.drain()
    await sim.send(funded, "funded", "!pull")
    check("4th pull inside window blocked", has(sim, "slow down"))
    await sim.send(funded, "funded", "!pull 10")
    check("window blocks across counts", has(sim, "slow down"))

    broke = 900_000_000_000_001_333
    await sim.send(broke, "broke", "!balance")
    await sim.send(OWNER_ID, "owner", f"!player setcoins <@{broke}> 0")
    await sim.send(broke, "broke", "!pull 10")
    check("multi pull with zero balance rejected", has(sim, "need"))
    await sim.send(broke, "broke", "!upgrades buy power")
    check("broke upgrade buy rejected", has(sim, "need"))

    # ------------------------------------------------------------------ hunt
    print("[hunt]")
    hunter = 900_000_000_000_001_000
    await sim.send(hunter, "hunter", "!balance")
    await sim.send_nowait(hunter, "hunter", "!hunt")
    await sim.send_nowait(hunter, "hunter", "!hunt")
    await sim.drain()
    n = await audit_count(sim, "hunt:", hunter)
    check("concurrent hunt rewards exactly once", n == 1, f"audit={n}")
    await sim.send(hunter, "hunter", "!hunt")
    check("hunt cooldown applies after race", has(sim, "slow down"))
    await sim.send(hunter, "hunter", "!profile")
    check("naked profile renders", has(sim, "level"))

    # ------------------------------------------------------------------ equipment
    print("[equipment]")
    await sim.send(VICTIM, "victim", "!equip 999999")
    check("equip nonexistent id", has(sim, "not found") or has(sim, "error"))
    await sim.send(VICTIM, "victim", "!equip -3")
    check("equip negative id rejected by converter", not has(sim, "equipped"))

    # get a real piece for PLAYER, then try to use it as VICTIM (IDOR)
    await sim.send(OWNER_ID, "owner", f"!grant <@{PLAYER_ID}> 50000")
    import re

    puller = await bot.economy.ensure_player(GUILD_ID, PLAYER_ID)
    for _ in range(40):
        await bot.gacha.pull(GUILD_ID, puller, 10)
        bot.cooldowns._hits.clear()
        pieces = await bot.equipment.owned(GUILD_ID, PLAYER_ID)
        if pieces:
            break
    await sim.send(PLAYER_ID, "player", "!equipment")
    equip_text = sim.http.last_text()
    m = re.search(r"#(\d+)", equip_text)
    if m:
        pid = m.group(1)
        await sim.send(VICTIM, "victim", f"!equip {pid}")
        check("cannot equip another player's item (IDOR)",
              not has(sim, "equipped") or "not found" in sim.http.last_text().lower(),
              sim.http.last_text()[:60])
        await sim.send(PLAYER_ID, "player", f"!equip {pid}")
        check("owner can equip own item", has(sim, "equipped"))
        await sim.send(PLAYER_ID, "player", f"!upgequip {pid}")
        check("forge works via command", has(sim, "forged"))
        await sim.send(PLAYER_ID, "player", f"!sellgear {pid}")
        check("selling equipped piece handled", has(sim, "sold"))
        bal = await sim.fetch_val(
            "SELECT balance FROM players WHERE user_id = :u AND guild_id = :g",
            {"u": PLAYER_ID, "g": GUILD_ID},
        )
        check("sell proceeds credited", bal is not None and bal > 0)
    else:
        check("equipment pull produced a piece", False, equip_text[:80])

    # ------------------------------------------------------------------ upgrades
    print("[upgrades]")
    await sim.send(VICTIM, "victim", "!upgrades buy")
    check("missing upgrade name prompts", has(sim, "missing argument"))
    await sim.send(VICTIM, "victim", "!upgrades buy LUCK")
    check("uppercase upgrade key works", has(sim, "luck") and has(sim, "level"))
    await sim.send(VICTIM, "victim", "!upgrades buy 'not an upgrade'")
    check("unknown upgrade rejected", has(sim, "unknown upgrade"))
    huge = "x" * 5000
    await sim.send(VICTIM, "victim", f"!upgrades buy {huge}")
    reply = sim.http.last_text()
    check("oversized name error stays bounded", len(reply) < 700, f"len={len(reply)}")

    # ------------------------------------------------------------------ huntbot
    print("[huntbot]")
    await sim.send(VICTIM, "victim", "!huntbot toggle")
    check("toggle without owning", has(sim, "don't own") or has(sim, "huntbot"))
    await sim.send(VICTIM, "victim", "!huntbot collect")
    check("collect without owning", has(sim, "don't own") or has(sim, "error"))
    await sim.send(VICTIM, "victim", "!huntbot buy")
    check("huntbot buy works", has(sim, "acquired"))
    await sim.send(VICTIM, "victim", "!huntbot buy")
    check("double buy rejected", has(sim, "already own"))
    await sim.send(VICTIM, "victim", "!huntbot upgrade")
    check("broke upgrade rejected", has(sim, "need"))

    await sim.send(broke, "broke", "!huntbot buy")
    check("broke huntbot buy rejected", has(sim, "need"))

    banked = 900_000_000_000_001_111
    await sim.send(banked, "banked", "!balance")
    await sim.send(OWNER_ID, "owner", f"!grant <@{banked}> 30000")
    await sim.send(banked, "banked", "!huntbot buy")
    await sim.huntbot_tick(user_id=banked, n=2)
    await sim.send_nowait(banked, "banked", "!huntbot collect")
    await sim.send_nowait(banked, "banked", "!huntbot collect")
    await sim.drain()
    n = await audit_count(sim, "huntbot:collect", banked)
    check("concurrent collect banks exactly once", n == 1, f"audit={n}")

    # ------------------------------------------------------------------ admin
    print("[admin]")
    await sim.send(PLAYER_ID, "player", "!badges grant")
    check("non-owner badges blocked", has(sim, "permission"))
    await sim.send(OWNER_ID, "owner", f"!badges grant <@{PLAYER_ID}> not_a_badge")
    check("unknown badge rejected", has(sim, "unknown badge"))
    await sim.send(OWNER_ID, "owner", f"!badges revoke <@{PLAYER_ID}> not_a_badge")
    check("revoking unknown badge fails cleanly", has(sim, "unknown badge"))
    await sim.send(OWNER_ID, "owner", f"!badges grant <@{PLAYER_ID}> veteran")
    await sim.send(OWNER_ID, "owner", f"!badges grant <@{PLAYER_ID}> veteran")
    held = await sim.fetch_val(
        "SELECT COUNT(*) FROM badges WHERE user_id = :u AND badge_key = 'veteran'",
        {"u": PLAYER_ID},
    )
    check("slash double badge grant deduped", int(held or 0) == 1, f"held={held}")
    await sim.send(OWNER_ID, "owner", "!badges")
    check("badge definition list renders", has(sim, "badge"))
    await sim.send(PLAYER_ID, "player", "!reload_content")
    check("non-owner reload_content blocked", has(sim, "permission"))

    # broken content reload must not kill the bot
    data_file = Path("bot/content/data/cards.json")
    backup = data_file.read_bytes()
    try:
        data_file.write_bytes(b"{ not json")
        await sim.send(OWNER_ID, "owner", "!reload_content")
        check("broken JSON reload aborts safely", has(sim, "aborted") or has(sim, "error"))
        await sim.send(VICTIM, "victim", "!balance")
        check("bot alive after failed reload", has(sim, "victim"))
    finally:
        data_file.write_bytes(backup)
    await sim.send(OWNER_ID, "owner", "!reload_content")
    check("restored content reloads", has(sim, "reloaded"))

    # ------------------------------------------------------------------ DMs
    print("[dms]")
    dm_user = 900_000_000_000_001_222
    await sim.send_dm(dm_user, "dmdude", "!daily")
    check("daily works in DM (global scope)", has(sim, "daily reward"))
    row = await sim.fetch_val(
        "SELECT COUNT(*) FROM players WHERE user_id = :u AND guild_id = 0",
        {"u": dm_user},
    )
    check("DM player stored under global scope", int(row or 0) == 1)
    await sim.send_dm(dm_user, "dmdude", f"!pay <@{PLAYER_ID}> 10")
    check("pay blocked in DM (guild-only)", has(sim, "server") or has(sim, "error"))
    await sim.send_dm(OWNER_ID, "owner", f"!grant <@{PLAYER_ID}> 10")
    check("grant blocked in DM (guild-only)", has(sim, "server") or has(sim, "error"))

    # ------------------------------------------------------------------ content features
    print("[content abuse]")
    # deterministic epic+: park pity one below the limit so the floor (legendary) fires
    await sim.send(OWNER_ID, "owner", f"!grant <@{PLAYER_ID}> 1000")
    await sim.db.execute(
        "UPDATE players SET pity_counter = :p WHERE guild_id = :g AND user_id = :u",
        {"p": bot_settings_gacha_pity() - 1, "g": GUILD_ID, "u": PLAYER_ID},
    )
    await sim.send(PLAYER_ID, "player", "!pull")
    pull_created = sim.http.last.created
    share_id = next(_find_custom_id(pull_created, 2), None)
    check("epic+ pull carries share button", bool(share_id))
    if share_id:
        await sim.component(PLAYER_ID, pull_created, 2, share_id)
        first = sim.last_interaction_payload()
        check("share posts publicly", first.get("type") == 4 and not (first.get("data", {}).get("flags", 0) & 64),
              str(first)[:80])
        await sim.component(PLAYER_ID, pull_created, 2, share_id)
        second = sim.last_interaction_payload()
        check("share is one-shot", (second.get("data", {}).get("flags", 0) & 64)
              or "already" in str(second).lower(), str(second)[:80])

    await sim.send(VICTIM, "victim", "!hunt_info")
    check("hunt_info shows today's modifier", "today's modifier" in sim.http.last_text().lower())
    fresh_col = 900_000_000_000_002_100
    await sim.send(fresh_col, "freshcol", "!balance")
    await sim.send(fresh_col, "freshcol", "!collection")
    check("collection of a cardless player renders with sets",
          "collection" in sim.http.last_text().lower()
          and any(s in sim.http.last_text() for s in ("Beast Court", "Prime Pantheon", "Streets")))
    await sim.send(fresh_col, "freshcol", "!profile")
    check("cardless profile renders (no set titles)",
          "level" in sim.http.last_text().lower()
          and "card sets" not in sim.http.last_text().lower())
    await sim.send(fresh_col, "freshcol", "!records")
    check("records command renders hall", "hall of records" in sim.http.last_text().lower())

    # encounter abuse: cooldown is spent even when the encounter is never answered
    enc_ab = 900_000_000_000_002_200
    await sim.send(enc_ab, "encab", "!balance")
    bot.content._encounter_chance = 1.0
    await sim.send(enc_ab, "encab", "!hunt")
    check("encounter card replaces hunt", "choose within 5 minutes" in sim.http.last_text().lower())
    await sim.send(enc_ab, "encab", "!hunt")
    check("unanswered encounter still spends the cooldown", has(sim, "slow down"))
    bot.content._encounter_chance = 0.0

    # ------------------------------------------------------------------ misc
    print("[misc]")
    n_sent = len(sim.http.sent)
    await sim.send(PLAYER_ID, "player", "!definitelynotacommand")
    check("unknown command is silent", len(sim.http.sent) == n_sent)
    await sim.send(PLAYER_ID, "player", "!help extra args here")
    check("help ignores extra args", has(sim, "help"))

    await sim.shutdown()

    print(f"\n{'=' * 60}\nRESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", *FAILED, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
