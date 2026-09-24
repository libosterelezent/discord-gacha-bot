"""Gacha Bot entry point.

Boots logging, builds the bot, and runs until interrupted — with
structured exit handling so the database and background tasks close
cleanly.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from bot.config import CONFIG
from bot.core.gacha_bot import GachaBot
from bot.core.logging_setup import setup_logging

logger = logging.getLogger("gacha.main")


def main() -> int:
    log = setup_logging(CONFIG.log_dir, CONFIG.log_level, CONFIG.log_max_bytes, CONFIG.log_backup_count)

    if not CONFIG.token:
        log.critical(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and add your bot token."
        )
        return 1

    bot = GachaBot()

    try:
        bot.run(CONFIG.token, log_handler=None)  # we handle logging ourselves
    except KeyboardInterrupt:
        log.info("Received Ctrl+C, shutting down.")
    except asyncio.CancelledError:
        log.info("Event loop cancelled, exiting.")
    except SystemExit as exc:
        log.info("System exit: %s", exc.code)
        return int(exc.code or 0)
    except Exception:
        log.exception("Fatal error during bot runtime")
        return 1
    finally:
        log.info("Goodbye.")
        logging.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
