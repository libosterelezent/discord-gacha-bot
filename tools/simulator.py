"""Local Discord simulator: run the real bot end-to-end without Discord.

Replaces the HTTP funnel (``bot.http.request``) with a recorder that
returns canned payloads, fabricates guild/channel/member/message objects,
and drives ``bot.process_commands`` — the exact pipeline a real gateway
message takes (prefix parsing, checks, converters, cooldowns, error
handlers, replies).

What the bot "sends" is captured and printed; assertions verify both the
replies and the database side-effects. A restart phase proves persisted
cooldowns survive a fresh process.

Usage:  DATABASE_URL=sqlite+aiosqlite:////tmp/gacha-sim.db python tools/simulator.py
"""
from __future__ import annotations

import asyncio
import datetime
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discord

from bot.core.gacha_bot import EXTENSIONS, GachaBot

# --- fake snowflakes ---------------------------------------------------------
GUILD_ID = 900_000_000_000_000_001
CHANNEL_ID = 900_000_000_000_000_002
BOT_ID = 900_000_000_000_000_003
OWNER_ID = 900_000_000_000_000_004  # matches the fake application owner
PLAYER_ID = 900_000_000_000_000_005

_ids = itertools.count(1)


def _snowflake() -> int:
    return 910_000_000_000_000_000 + next(_ids)


def _user_payload(uid: int, name: str) -> dict:
    return {"id": str(uid), "username": name, "discriminator": "0", "global_name": None,
            "avatar": None, "bot": False, "system": False, "public_flags": 0}


def _member_payload(uid: int, name: str) -> dict:
    return {"user": _user_payload(uid, name), "roles": [],
            "joined_at": "2026-01-01T00:00:00+00:00",
            "deaf": False, "mute": False, "flags": 0, "pending": False}


def _message_payload(content: str, member_payload: dict, reply_to: int | None = None) -> dict:
    payload = {
        "id": str(_snowflake()),
        "type": 0,
        "channel_id": str(CHANNEL_ID),
        "author": member_payload,
        "content": content,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "edited_timestamp": None,
        "tts": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [],
        "embeds": [],
        "nonce": None,
        "pinned": False,
        "flags": 0,
    }
    if reply_to is not None:
        payload["referenced_message"] = {
            **_message_payload("", _user_payload(BOT_ID, "GachaHunter")), "id": str(reply_to),
        }
        payload["message_reference"] = {"message_id": str(reply_to),
                                        "channel_id": str(CHANNEL_ID), "guild_id": str(GUILD_ID)}
    return payload


# --- simulated HTTP ----------------------------------------------------------
class SentMessage:
    def __init__(self, payload: dict, created: dict | None = None) -> None:
        self.raw = payload
        self.created = created  # the fabricated message the API would return
        self.content = payload.get("content", "")
        self.embeds = payload.get("embeds", [])
        self.components = payload.get("components", [])
        self.flags = payload.get("flags", 0)

    @staticmethod
    def _component_text(node) -> str:
        """Recursively collect TextDisplay content from a CV2 payload."""
        out: list[str] = []
        if isinstance(node, dict):
            if "content" in node and isinstance(node["content"], str):
                out.append(node["content"])
            for child in node.get("components", []):
                out.extend(SentMessage._component_text(child))
        elif isinstance(node, list):
            for child in node:
                out.extend(SentMessage._component_text(child))
        return out

    def text(self) -> str:
        parts = [self.content]
        for embed in self.embeds:
            parts.append(embed.get("title", "") or "")
            parts.append(embed.get("description", "") or "")
        parts.extend(self._component_text(self.components))
        return " | ".join(p for p in parts if p)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"SentMessage({self.text()[:120]!r})"


class SimHTTP:
    """Records every request; answers with plausible payloads.

    ``fail_plan`` maps path substrings to deques of exceptions raised on
    the next matching requests — used to fault-inject 429s/HTTP errors
    and exercise the outbound limiter's retry logic.
    """

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.interaction_replies: list[tuple[str, dict]] = []
        self.calls: list[tuple[str, str]] = []
        self.webhook_calls: list[tuple[str, str]] = []
        self.attempts: list[tuple[str, str]] = []
        self.fail_plan: dict[str, list[BaseException]] = {}

    def inject_failures(self, path_substring: str, *exceptions: BaseException) -> None:
        self.fail_plan.setdefault(path_substring, []).extend(exceptions)

    async def request(self, route, *, files=None, form=None, **kwargs):
        method, path = route.method, route.url.split("discord.com")[-1]
        path = path.replace("//", "/")
        self.attempts.append((method, path))
        for fragment, queue in self.fail_plan.items():
            if fragment in path and queue:
                raise queue.pop(0)
        self.calls.append((method, path))
        payload = kwargs.get("json") or {}
        if path.startswith("/api/v10/channels/") and path.endswith("/messages") and method == "POST":
            reply = {
                **_message_payload(payload.get("content", ""), _user_payload(BOT_ID, "GachaHunter")),
                "embeds": payload.get("embeds", []),
                "components": payload.get("components", []),
                "flags": payload.get("flags", 0),
            }
            self.sent.append(SentMessage(payload, created=reply))
            return reply
        if path.startswith("/api/v10/interactions/") and method == "POST":
            self.interaction_replies.append((path, payload))
            return {}
        if "/applications/" in path and path.endswith("/commands") and method == "PUT":
            return []  # tree.sync() expects a list of command payloads
        if path == "/api/v10/oauth2/applications/@me":
            return {"id": str(BOT_ID), "name": "GachaHunterSim", "verify_key": "x",
                    "flags": 0, "summary": "", "description": "",
                    "icon": None, "bot_public": True, "bot_require_code_grant": False,
                    "owner": _user_payload(OWNER_ID, "owner"), "team": None,
                    "guild_id": None, "primary_sku_id": None, "slug": None,
                    "cover_image": None, "tags": [], "custom_install_url": None,
                    "install_params": None, "role_connections_verification_url": None}
        if method == "GET":
            return {}
        return {}

    @property
    def last(self) -> SentMessage:
        return self.sent[-1]

    def last_text(self) -> str:
        return self.sent[-1].text() if self.sent else ""


class ShimWebhookAdapter:
    """Replaces the interaction/webhook adapter so interaction responses
    (send_message, edit_message, followups) are recorded instead of sent.

    Subclasses the real adapter and overrides only ``request`` — every
    protocol method (create_interaction_response, execute_webhook, …)
    funnels through it, keeping signatures correct for free.
    """

    def __init__(self, http: SimHTTP) -> None:
        from discord.webhook.async_ import AsyncWebhookAdapter

        # dynamic base to keep a single class definition
        base = AsyncWebhookAdapter
        self._http = http
        self._locks = base.__new__(type(self))._locks if False else None
        import weakref

        self._locks = weakref.WeakValueDictionary()

    async def request(self, route, session=None, *, payload=None, multipart=None, **kwargs):
        method = route.method
        path = route.url.split("discord.com")[-1].replace("//", "/")
        if payload is None and multipart:
            payload = {
                p.get("name"): p.get("value")
                for p in multipart
                if isinstance(p, dict) and "payload_json" not in p.get("name", "")
            }
            for p in multipart:
                if isinstance(p, dict) and p.get("name") == "payload_json":
                    import json as _json

                    try:
                        payload = _json.loads(p.get("value", "{}"))
                    except (TypeError, ValueError):
                        pass
        self._http.interaction_replies.append((f"{method} {path}", payload or {}))
        self._http.webhook_calls.append((method, path))
        if method == "POST" and "/webhooks/" in path:
            return _message_payload("", _user_payload(BOT_ID, "GachaHunter"))
        return {}


# --- simulator ----------------------------------------------------------------
class Simulator:
    """Boots the real GachaBot against the fake gateway/HTTP.

    The database comes from CONFIG (DATABASE_URL env) — set it before
    importing any bot module, since bot.config reads it at import time.
    """

    def __init__(self) -> None:
        self.http = SimHTTP()
        self.bot: GachaBot | None = None
        self.presence: dict = {}

    async def boot(self) -> None:
        bot = GachaBot()
        await bot._async_setup_hook()  # bind the running loop (normally done in login)
        bot.http.request = self.http.request  # type: ignore[method-assign]
        client_user = discord.ClientUser(
            state=bot._connection,
            data={**_user_payload(BOT_ID, "GachaHunter"), "verified": True,
                  "mfa_enabled": True, "email": None},
        )
        # Client.user is a read-only property backed by the connection state
        bot._connection.user = client_user  # type: ignore[attr-defined]
        bot._connection.application_id = BOT_ID  # type: ignore[attr-defined]
        self._client_user = client_user
        guild_data = {
            "id": str(GUILD_ID), "name": "Sim Guild", "owner_id": str(OWNER_ID),
            "afk_channel_id": None, "afk_timeout": 0, "widget_enabled": False,
            "widget_channel_id": None, "verification_level": 0, "default_message_notifications": 0,
            "explicit_content_filter": 0, "roles": [], "emojis": [], "features": [],
            "mfa_level": 0, "system_channel_id": None, "premium_tier": 0,
            "preferred_locale": "en-US", "nsfw_level": 0, "premium_progress_bar_enabled": False,
            "members": [
                _member_payload(OWNER_ID, "owner"),
                _member_payload(PLAYER_ID, "player"),
                # the bot itself must be a guild member (ctx.me / clean_prefix)
                {"user": _user_payload(BOT_ID, "GachaHunter") | {"bot": True},
                 "roles": [], "joined_at": "2026-01-01T00:00:00+00:00",
                 "deaf": False, "mute": False, "flags": 0, "pending": False},
            ],
            "channels": [], "threads": [], "stickers": [], "voice_states": [], "large": False,
            "unavailable": False, "member_count": 3, "application_id": str(BOT_ID),
            "joined_at": "2026-01-01T00:00:00+00:00", "max_members": 100, "vanity_url_code": None,
            "description": None, "banner": None, "splash": None, "discovery_splash": None,
            "rules_channel_id": None, "public_updates_channel_id": None,
            "max_presences": None, "presences": [], "stage_instances": [], "guild_scheduled_events": [],
        }
        self.guild = discord.Guild(state=bot._connection, data=guild_data)
        self.channel = discord.TextChannel(
            state=bot._connection, guild=self.guild,
            data={"id": str(CHANNEL_ID), "type": 0, "name": "general", "position": 0,
                  "permission_overwrites": [], "rate_limit_per_user": 0, "nsfw": False,
                  "topic": "", "last_message_id": None, "parent_id": None,
                  "last_pin_timestamp": None},
        )
        self.state = bot._connection

        await bot.db.connect()
        await bot.cooldowns.load()
        for service in bot._services:
            await service.on_start()
        await bot.sink.start()
        for extension in EXTENSIONS:
            await bot.load_extension(extension)

        # interaction/webhook responses captured instead of performed
        from discord.webhook.async_ import AsyncWebhookAdapter, async_context

        shim_cls = type("ShimAdapter", (AsyncWebhookAdapter,), {"request": ShimWebhookAdapter.request})
        adapter = shim_cls()
        adapter._http = self.http  # type: ignore[attr-defined]
        async_context.set(adapter)
        # sink channel resolution: our fabricated channel is not in the cache
        bot.get_channel = lambda cid: self.channel if cid == CHANNEL_ID else None  # type: ignore[method-assign]
        self.bot = bot

    async def shutdown(self) -> None:
        if self.bot is not None:
            await self.bot.close()
            self.bot = None

    # -- driving ---------------------------------------------------------------

    async def send(self, user_id: int, name: str, content: str) -> None:
        assert self.bot is not None
        message = discord.Message(
            state=self.state, channel=self.channel,
            data=_message_payload(content, _user_payload(user_id, name)),
        )
        self.bot.dispatch("message", message)
        await asyncio.sleep(0)  # let the command task run
        await self._drain()

    async def _drain(self) -> None:
        """Wait until command processing settles.

        Waits for discord.py event tasks ('discord.py: on_message' etc.)
        and interaction invokers ('CommandTree-invoker'); long-lived
        service tasks (huntbot loop, sink worker) never finish by design
        and must not block the drain.
        """
        for _ in range(50):
            pending = [
                t for t in asyncio.all_tasks()
                if t is not asyncio.current_task() and not t.done()
                and (t.get_name().startswith("discord.py:")
                     or t.get_name().startswith("CommandTree-invoker")
                     or t.get_name().startswith("python"))
            ]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    async def huntbot_tick(self, guild_id: int = GUILD_ID, user_id: int = PLAYER_ID, n: int = 3) -> None:
        """Drive the real huntbot tick path N times (skips the 5min wait)."""
        assert self.bot is not None
        for _ in range(n):
            await self.bot.huntbot._tick_user(guild_id, user_id, 0.0)

    # -- interactions ------------------------------------------------------------

    def _interaction_payload(self, user_id: int, name: str, *, kind: int = 2,
                              data: dict | None = None, message: dict | None = None) -> dict:
        payload = {
            "id": str(_snowflake()),
            "type": kind,
            "token": f"sim-token-{next(_ids)}",
            "version": 1,
            "application_id": str(BOT_ID),
            "attachment_size_limit": 26_214_400,
            "guild_id": str(GUILD_ID),
            "channel_id": str(CHANNEL_ID),
            # channel must be a full object — Interaction._from_data resolves
            # the channel (and its id) from here, not from channel_id
            "channel": {"id": str(CHANNEL_ID), "type": 0, "name": "general",
                        "last_message_id": None, "nsfw": False, "position": 0,
                        "permission_overwrites": [], "rate_limit_per_user": 0,
                        "topic": "", "parent_id": None, "flags": 0},
            "locale": "en-US",
            "guild_locale": "en-US",
            "member": _member_payload(user_id, "member"),
            "data": data or {},
        }
        if message is not None:
            payload["message"] = message
        return payload

    async def slash(self, user_id: int, command: str, **options) -> None:
        """Dispatch a real APPLICATION_COMMAND interaction through the tree."""
        assert self.bot is not None
        data = {"id": str(_snowflake()), "name": command, "type": 1}
        if options:
            data["options"] = [
                {"name": key, "type": 4, "value": value} for key, value in options.items()
            ]
        self.bot._connection.parse_interaction_create(
            self._interaction_payload(user_id, command, kind=2, data=data)
        )
        await asyncio.sleep(0)
        await self._drain()

    async def component(self, user_id: int, message_payload: dict, component_type: int,
                        custom_id: str, values: list[str] | None = None) -> None:
        """Dispatch a MESSAGE_COMPONENT interaction (button/select press)."""
        assert self.bot is not None
        data: dict = {"component_type": component_type, "custom_id": custom_id}
        if values is not None:
            data["values"] = values
        self.bot._connection.parse_interaction_create(
            self._interaction_payload(user_id, custom_id, kind=3, data=data, message=message_payload)
        )
        await asyncio.sleep(0)
        await self._drain()

    def last_interaction_payload(self) -> dict:
        return self.http.interaction_replies[-1][1]

    def interaction_text(self) -> str:
        parts: list[str] = []
        for _path, payload in self.http.interaction_replies:
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            if isinstance(data, dict):
                parts.append(data.get("content", "") or "")
                for embed in data.get("embeds", []):
                    parts.append(embed.get("title", "") or "")
                    parts.append(embed.get("description", "") or "")
        return " | ".join(p for p in parts if p)

    # -- gateway-ish events --------------------------------------------------------

    async def ready(self) -> None:
        """Fire on_ready with a stub websocket capturing the presence update."""
        assert self.bot is not None
        recorded: dict = {}

        class FakeWS:
            open = False  # so Client.close() skips websocket teardown

            async def change_presence(self, *, activity=None, status=None, **kwargs):
                recorded["activity"] = getattr(activity, "name", activity)
                recorded["status"] = status

        self.bot.ws = FakeWS()  # type: ignore[assignment]  # Client.ws is set in connect()
        self.bot.dispatch("ready")
        await asyncio.sleep(0)
        await self._drain()
        self.presence = recorded

    # -- assertions ------------------------------------------------------------

    @property
    def db(self):
        assert self.bot is not None
        return self.bot.db

    async def fetch_val(self, sql: str, params: dict | None = None):
        return await self.db.fetch_val(sql, params or {})
