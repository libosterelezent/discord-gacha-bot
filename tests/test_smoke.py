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


class ServiceSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.db = Database(_temp_db_url())
        await self.db.connect()
        self.registry = ContentRegistry.load()
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

    async def test_hunt_roundtrip(self) -> None:
        from bot.models.player import StatProfile

        player = await self.economy.ensure_player(self.GUILD, self.USER)
        profile = StatProfile.compose(player, self.registry.upgrade_effects())
        result = await self.hunt.hunt(self.GUILD, player, profile)
        self.assertIsNotNone(result.enemy)

    @unittest.expectedFailure  # TODO: fixed by global-badge sentinel change
    async def test_badges_no_duplicate_grant(self) -> None:
        await self.badges.grant(self.GUILD, self.USER, "veteran")
        await self.badges.grant(self.GUILD, self.USER, "veteran")
        held = await self.badges.player_badges(self.USER, self.GUILD)
        keys = [spec.key for spec, _ in held]
        self.assertEqual(keys.count("veteran"), 1)

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
