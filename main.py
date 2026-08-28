import asyncio
import signal
import sys
import os
import discord

from config import DISCORD_TOKEN
from core.bot import FloryGuardBot
from utils.logger import logger


async def main():
    if not DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN is missing in environment/.env file!")
        sys.exit(1)

    bot = FloryGuardBot()

    # Graceful shutdown handler
    loop = asyncio.get_running_loop()

    def handle_shutdown():
        logger.info("Received termination signal. Shutting down gracefully...")
        asyncio.create_task(bot.close())

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_shutdown)

    try:
        logger.info("Starting FloryGuard Security Bot...")
        await bot.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.critical("Invalid Discord Token provided! Please check DISCORD_TOKEN in .env")
    except Exception as e:
        logger.critical(f"Fatal error during bot runtime: {e}", exc_info=True)
    finally:
        if not bot.is_closed():
            await bot.close()
        logger.info("FloryGuardBot has successfully shut down.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("FloryGuardBot stopped by user.")
