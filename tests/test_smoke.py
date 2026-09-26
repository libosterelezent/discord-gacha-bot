"""Headless smoke tests: content registry, database and services.

Run with:  python -m unittest discover -s tests -v
(no Discord token or network required)
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from bot.content.registry import ContentRegistry
from bot.core.cooldowns import CooldownManager
from bot.core.database import Database
from bot.core.events import EventBus
from bot.services.badges import BadgeService
from bot.services.economy import EconomyService
from bot.services.gacha import GachaService
from bot.services.hunt import HuntService
from bot.services.huntbot import HuntBotService


def _temp_db_url() -> str:
    tmp = tempfile.mkdtemp(prefix="gacha-test-")
    return f"sqlite+aiosqlite:///{Path(tmp) / 'test.db'}"


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ContentRegistry.load()

    def test_rarity_tiers_load(self) -> None:
        keys = [r.key for r in self.registry.rarities]
        self.assertEqual(keys, sorted(keys, key=lambda k: self.registry.rarity(k).tier))
        self.assertIn("legendary", keys)  # referenced by gacha.pity_floor

    def test_upgrade_keys_referenced_by_code_exist(self) -> None:
        for key in ("power", "luck", "greed", "swiftness", "harvest"):
            self.assertIsNotNone(self.registry.upgrade(key), f"missing upgrade '{key}'")

    def test_every_rarity_has_cards_and_enemies(self) -> None:
        for rarity in self.registry.rarities:
            self.assertTrue(
                self.registry.enemies_by_rarity(rarity),
                f"rarity '{rarity.key}' has no enemies — hunts would fall back",
            )
            self.assertTrue(
                self.registry.cards_by_rarity(rarity),
                f"rarity '{rarity.key}' has no cards — pulls would be odd",
            )

    def test_equipment_slots_valid(self) -> None:
        for template in self.registry.all_equipment():
            self.assertIn(template.slot, ("weapon", "armor", "amulet"))

    def test_card_sets_reference_real_cards(self) -> None:
        sets = self.registry.all_sets()
        self.assertGreater(len(sets), 0)
        for card_set in sets:
            for key in card_set.cards:
                self.assertIsNotNone(self.registry.card(key), f"{card_set.key} -> {key}")

    def test_set_bonuses_aggregate_from_owned_cards(self) -> None:
        beast = self.registry.set_("beast_court")
        owned = set(beast.cards[:2])  # 2 of 3 -> tier 1 only
        bonuses, titles = self.registry.set_bonuses(owned)
        self.assertAlmostEqual(bonuses.get("coin_pct", 0.0), 0.03)
        self.assertEqual(titles, ["Friend of Fangs"])
        full, titles2 = self.registry.set_bonuses(set(beast.cards))
        self.assertAlmostEqual(full.get("coin_pct", 0.0), 0.03)
        self.assertAlmostEqual(full.get("luck_pct", 0.0), 0.01)
        self.assertIn("Court Whisperer", titles2)

    def test_hunt_modifier_pool_deterministic_per_date(self) -> None:
        from datetime import date

        pool = self.registry.all_modifiers()
        self.assertGreater(len(pool), 4)
        today = date(2026, 9, 26)
        first = self.registry.modifier_for_date(today)
        self.assertIs(first, self.registry.modifier_for_date(today))  # same object: deterministic
        # rotation actually varies across dates (sample a week)
        picks = {self.registry.modifier_for_date(date(2026, 9, d)).key for d in range(20, 27)}
        self.assertGreater(len(picks), 1)

    def test_equipment_roll_percentile_math(self) -> None:
        import random

        template = self.registry.equipment_template("war_scythe")
        rarity = self.registry.rarity("rare")
        low = template.roll(random.Random(0), rarity)
        low.attack = round(template.base_attack * rarity.stat_multiplier * 0.85)
        low.defense = 0
        low.luck = 0
        self.assertAlmostEqual(self.registry.equipment_roll_percentile(low), 0.0, places=1)

        high = template.roll(random.Random(0), rarity)
        high.attack = round(template.base_attack * rarity.stat_multiplier * 1.15)
        high.defense = 0
        high.luck = 0
        self.assertAlmostEqual(self.registry.equipment_roll_percentile(high), 1.0, places=1)

        mid = template.roll(random.Random(0), rarity)
        mid.attack = round(template.base_attack * rarity.stat_multiplier * 1.0)
        mid.defense = 0
        mid.luck = 0
        self.assertAlmostEqual(self.registry.equipment_roll_percentile(mid), 0.5, delta=0.02)

        # forging must not change the percentile
        forged = template.roll(random.Random(0), rarity)
        forged.attack = round(template.base_attack * rarity.stat_multiplier * 1.0)
        forged.defense = 0
        forged.luck = 0
        forged.level = 5
        from bot.config import SETTINGS

        factor = (1 + SETTINGS.equipment.forge_growth) ** 5
        forged.attack = round(forged.attack * factor)
        self.assertAlmostEqual(
            self.registry.equipment_roll_percentile(forged), 0.5, delta=0.03
        )

    def test_stat_profile_applies_set_bonuses(self) -> None:
        from bot.models.player import Player, StatProfile

        player = Player(user_id=1, level=10)
        base = StatProfile.compose(player, {}, set_bonuses={"coin_pct": 0.03, "xp_pct": 0.02})
        self.assertAlmostEqual(base.coin_multiplier, 1.10 + 0.03)  # level 10 +1% per + 3%
        self.assertAlmostEqual(base.xp_multiplier, 1.02)
        self.assertAlmostEqual(base.luck, 0.0)


class ServiceSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.db = Database(_temp_db_url())
        await self.db.connect()
        self.registry = ContentRegistry.load()
        self.registry._encounter_chance = 0.0  # deterministic hunts in tests
        self.bus = EventBus()
        self.cooldowns = CooldownManager.__new__(CooldownManager)  # replaced below
        from bot.config import SETTINGS

        self.settings = SETTINGS
        self.cooldowns = CooldownManager(SETTINGS)
        self.economy = EconomyService(self.db, self.registry, SETTINGS, self.bus, self.cooldowns)
        self.gacha = GachaService(self.db, self.registry, SETTINGS, self.bus, self.cooldowns)
        self.hunt = HuntService(
            self.db, self.registry, SETTINGS, self.bus, self.cooldowns, economy=self.economy
        )
        self.huntbot = HuntBotService(self.db, self.registry, SETTINGS, self.bus, self.cooldowns)
        self.badges = BadgeService(self.db, self.registry, SETTINGS, self.bus, self.cooldowns)
        for service in (self.economy, self.gacha, self.hunt, self.huntbot, self.badges):
            await service.on_start()

    async def asyncTearDown(self) -> None:
        await self.huntbot.close()
        await self.db.close()

    GUILD = 100
    USER = 200

    async def test_player_lifecycle(self) -> None:
        player = await self.economy.ensure_player(self.GUILD, self.USER)
        self.assertEqual(player.balance, self.settings.economy.starting_balance)
        again = await self.economy.ensure_player(self.GUILD, self.USER)
        self.assertEqual(again.user_id, self.USER)

    async def test_deposit_withdraw(self) -> None:
        player = await self.economy.ensure_player(self.GUILD, self.USER)
        await self.economy.deposit(self.GUILD, self.USER, 500, "test")
        bal = await self.economy.balance(self.GUILD, self.USER)
        self.assertEqual(bal, player.balance + 500)
        await self.economy.withdraw(self.GUILD, self.USER, 100, "test")
        self.assertEqual(await self.economy.balance(self.GUILD, self.USER), bal - 100)
        with self.assertRaises(Exception):
            await self.economy.withdraw(self.GUILD, self.USER, 10**12, "test")

    async def test_gacha_pull_and_pity(self) -> None:
        player = await self.economy.ensure_player(self.GUILD, self.USER)
        await self.economy.deposit(self.GUILD, self.USER, 100_000, "test")
        player = await self.economy.ensure_player(self.GUILD, self.USER)
        for _ in range(3):
            session = await self.gacha.pull(self.GUILD, player, 1)
            self.assertEqual(len(session.outcomes), 1)
        self.cooldowns._hits.clear()  # reset sliding window for the pity pull
        player.pity_counter = self.settings.gacha.pity_limit - 1
        session = await self.gacha.pull(self.GUILD, player, 1)
        self.assertTrue(any(o.pity_triggered for o in session.outcomes))
        self.assertEqual(player.pity_counter, 0)

    async def test_gacha_pull_concurrent_payment_safe(self) -> None:
        player = await self.economy.ensure_player(self.GUILD, 777)
        await self.economy.deposit(self.GUILD, 777, 150, "test")
        player = await self.economy.ensure_player(self.GUILD, 777)
        start = player.balance
        self.cooldowns._hits.clear()
        p1 = await self.economy.ensure_player(self.GUILD, 777)
        results = await asyncio.gather(
            self.gacha.pull(self.GUILD, p1, 1),
            self.gacha.pull(self.GUILD, p1, 1),
            return_exceptions=True,
        )
        ok = [r for r in results if not isinstance(r, Exception)]
        # both may succeed only if the balance covered both pulls
        final = await self.economy.balance(self.GUILD, 777)
        self.assertGreaterEqual(final, 0)
        self.assertEqual(final, start - 100 * len(ok))

    async def test_records_best_and_first_semantics(self) -> None:
        from bot.services.records import RecordService

        records = RecordService(self.db, self.registry, self.settings, self.bus, self.cooldowns)
        await records.on_start()
        self.assertTrue(await records.submit_max(1, "hunt_best", "Largest hunt", 10, 500))
        self.assertFalse(await records.submit_max(1, "hunt_best", "Largest hunt", 20, 400))
        self.assertTrue(await records.submit_max(1, "hunt_best", "Largest hunt", 20, 600))
        rows = await records.all_records(1)
        best = next(r for r in rows if r["key"] == "hunt_best")
        self.assertEqual((best["user_id"], best["value"]), (20, 600))

        self.assertTrue(await records.claim_first(1, "first_mythic", "First Mythic", 10))
        self.assertFalse(await records.claim_first(1, "first_mythic", "First Mythic", 99))
        first = next(r for r in await records.all_records(1) if r["key"] == "first_mythic")
        self.assertEqual(first["user_id"], 10)

    async def test_encounter_resolution_grants_rewards(self) -> None:
        from bot.models.player import StatProfile

        player = await self.economy.ensure_player(self.GUILD, self.USER)
        profile = StatProfile.compose(player, self.registry.upgrade_effects())
        encounter = next(
            e for e in self.registry.all_encounters()
            if e.key == "wandering_merchant"
        )
        haggle = encounter.choice("haggle")
        assert haggle is not None
        self.assertGreater(haggle.coins[0], 0)
        balance_before = await self.economy.balance(self.GUILD, self.USER)
        resolution = await self.hunt.resolve_encounter(
            self.GUILD, player, profile, encounter, "haggle"
        )
        self.assertEqual(resolution.choice.key, "haggle")
        self.assertGreaterEqual(resolution.coins, haggle.coins[0])
        balance_after = await self.economy.balance(self.GUILD, self.USER)
        self.assertEqual(balance_after, balance_before + resolution.coins)
        audited = await self.db.fetch_val(
            "SELECT COUNT(*) FROM economy_log WHERE reason = 'encounter:wandering_merchant'"
        )
        self.assertEqual(int(audited or 0), 1)

    async def test_hunt_roundtrip(self) -> None:
        from bot.models.player import StatProfile

        player = await self.economy.ensure_player(self.GUILD, self.USER)
        profile = StatProfile.compose(player, self.registry.upgrade_effects())
        result = await self.hunt.hunt(self.GUILD, player, profile)
        self.assertIsNotNone(result.enemy)

    async def test_badges_no_duplicate_grant(self) -> None:
        await self.badges.grant(self.GUILD, self.USER, "veteran")
        await self.badges.grant(self.GUILD, self.USER, "veteran")
        held = await self.badges.player_badges(self.USER, self.GUILD)
        keys = [spec.key for spec, _ in held]
        self.assertEqual(keys.count("veteran"), 1)
        # revoke actually removes the global badge
        await self.badges.revoke(self.GUILD, self.USER, "veteran")
        held = await self.badges.player_badges(self.USER, self.GUILD)
        self.assertEqual([spec.key for spec, _ in held], [])

    async def test_transfer_concurrent_cannot_double_spend(self) -> None:
        sender = await self.economy.ensure_player(self.GUILD, self.USER)
        await self.economy.deposit(self.GUILD, self.USER, 1_000, "test")
        sender = await self.economy.ensure_player(self.GUILD, self.USER)
        start = sender.balance
        results = await asyncio.gather(
            self.economy.transfer(self.GUILD, sender, 300, 1_000),
            self.economy.transfer(self.GUILD, sender, 1_000, 1_001),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(failures), 1, f"expected exactly one failure, got {results}")
        final = await self.economy.balance(self.GUILD, self.USER)
        self.assertGreaterEqual(final, 0)
        # exactly one amount left the account
        self.assertIn(final, (start - 1_000, start - 300))

    async def test_cooldowns_persist_across_manager_restarts(self) -> None:
        from bot.config import SETTINGS

        persistent = CooldownManager(SETTINGS, self.db)
        await persistent.load()
        await persistent.trigger(self.GUILD, self.USER, "daily")
        self.assertGreater(persistent.remaining(self.GUILD, self.USER, "daily"), 0)

        reborn = CooldownManager(SETTINGS, self.db)
        await reborn.load()
        self.assertGreater(reborn.remaining(self.GUILD, self.USER, "daily"), 0)
        with self.assertRaises(Exception):
            reborn.check(self.GUILD, self.USER, "daily")

    async def test_huntbot_buy_and_collect(self) -> None:
        player = await self.economy.ensure_player(self.GUILD, self.USER)
        await self.economy.deposit(self.GUILD, self.USER, 50_000, "test")
        player = await self.economy.ensure_player(self.GUILD, self.USER)
        cost = await self.huntbot.buy(self.GUILD, player)
        self.assertGreater(cost, 0)
        state = await self.huntbot.require_state(self.GUILD, self.USER)
        self.assertTrue(state.active)
        # simulate one tick directly and collect
        await self.huntbot._tick_user(self.GUILD, self.USER, 0.0)
        coins, items = await self.huntbot.collect(self.GUILD, player)
        self.assertGreaterEqual(coins, 0)


if __name__ == "__main__":
    unittest.main()
