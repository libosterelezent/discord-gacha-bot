"""Runtime verification of the Components V2 presentation layer.

Instantiates every view builder with fabricated game data and asserts the
LayoutView builds a component tree (discord.py validates item types/arity
inside add_item, so this exercises the real construction path). The help
menu is built against a real GachaBot with all cogs loaded.
"""
from __future__ import annotations

import random
import unittest

from bot.content.registry import ContentRegistry
from bot.core.exceptions import CooldownError
from bot.models.player import Player, StatProfile
from bot.services.gacha import PullOutcome, PullSession
from bot.services.hunt import HuntResult
from bot.services.huntbot import HuntBotState
from bot.ui import components as ui
from bot.ui.theme import Theme


class ViewBuildersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ContentRegistry.load()
        rng = random.Random(42)
        self.rarity = self.registry.rarity("epic")
        self.equipment = self.registry.equipment_template("storm_lance").roll(rng, self.rarity)

        self.player = Player(user_id=1, guild_id=1, balance=12_345, shards=67, xp=50, level=7)
        self.profile = StatProfile.compose(self.player, self.registry.upgrade_effects())

        self.session = PullSession(cost=100)
        self.session.add(PullOutcome(rarity=self.rarity, card=self.registry.card("abyssal_seer"), is_new_card=True))
        self.session.add(PullOutcome(rarity=self.registry.rarity("rare"), equipment=self.equipment))

        self.hunt = HuntResult(
            enemy=self.registry.enemy("frost_lich"), success=True, coins=250, xp=40,
            power=100, enemy_power=200, drops=[self.equipment], card_key=None, level_up=None,
        )
        self.state = HuntBotState(
            user_id=1, guild_id=1, level=3, active=True, battery=5, last_tick=None,
            unclaimed_coins=420, unclaimed_items=["iron_sword"], hunts_done=12,
        )

    @staticmethod
    def _built(view) -> int:
        comps = view.to_components()
        assert comps, "view produced no components"
        return len(comps)

    def test_plain_is_passthrough(self) -> None:
        self.assertEqual(ui.plain("x"), "x")

    def test_card_view(self) -> None:
        self.assertGreaterEqual(self._built(ui.card_view("## T", "body", Theme.success, "foot")), 1)

    def test_profile_view(self) -> None:
        self.assertGreaterEqual(
            self._built(ui.profile_view(self.player, self.profile, "Tester", None, huntbot=self.state, badges=["\U0001f3c5 **VIP**"])), 1
        )

    def test_pull_view(self) -> None:
        self.assertGreaterEqual(self._built(ui.pull_view(self.session, "\u2728", 90)), 1)

    def test_hunt_view(self) -> None:
        self.assertGreaterEqual(self._built(ui.hunt_view(self.hunt, "\U0001fa99")), 1)

    def test_leaderboard_equipment_collection_views(self) -> None:
        self.assertGreaterEqual(self._built(ui.leaderboard_view("Top", ["\U0001f947 **a**"])), 1)
        self.assertGreaterEqual(self._built(ui.equipment_view("Tester", ["\u2022 item"], "note")), 1)
        self.assertGreaterEqual(self._built(ui.collection_view("Tester", ["\u26ab Card"], 5, 24)), 1)

    def test_huntbot_and_upgrades_and_shard_views(self) -> None:
        self.assertGreaterEqual(self._built(ui.huntbot_view(self.state, 80, "1,000", 24)), 1)
        self.assertGreaterEqual(self._built(ui.upgrades_view(["\u2694\ufe0f Power"], "hint")), 1)
        self.assertGreaterEqual(self._built(ui.shard_info_view(30, "spend them")), 1)

    def test_theme_helpers(self) -> None:
        embed = Theme.error_embed("boom")
        self.assertEqual(embed.colour.value, Theme.error)
        self.assertIn("boom", embed.description)
        bar = Theme.progress_bar(5, 10, width=4)
        self.assertEqual(len(bar), 4)
        self.assertIn("\u2588", bar)

    def test_exception_messages_render(self) -> None:
        self.assertIn("seconds", str(CooldownError(12.5)))


class HelpMenuTest(unittest.IsolatedAsyncioTestCase):
    async def test_help_menu_builds_against_loaded_bot(self) -> None:
        from bot.core.gacha_bot import EXTENSIONS, GachaBot
        from bot.ui.help import HelpMenuView, build_categories

        bot = GachaBot()
        for ext in EXTENSIONS:
            await bot.load_extension(ext)
        categories = build_categories(bot)
        self.assertGreater(len(categories), 5, "expected every cog category")

        home = HelpMenuView(bot, "!")
        self.assertTrue(home.to_components())

        for category in list(categories)[:3]:
            view = HelpMenuView(bot, "!", category)
            self.assertTrue(view.to_components(), f"category {category} failed to build")
        await bot.close()


if __name__ == "__main__":
    unittest.main()
