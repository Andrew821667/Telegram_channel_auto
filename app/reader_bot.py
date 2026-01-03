"""
Reader Bot Entry Point.

Main bot for readers - personalization, search, digests.
"""

import asyncio
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.bot.reader_handlers import router
from app.models.database import AsyncSessionLocal, init_db
from app.utils.logger import logger


# Database middleware
async def db_middleware(handler, event, data):
    """Provide database session for handlers."""
    async with AsyncSessionLocal() as session:
        data['db'] = session
        try:
            return await handler(event, data)
        finally:
            await session.close()


async def main():
    """Main entry point for reader bot."""
    logger.info("reader_bot_starting", token_prefix=settings.reader_bot_token[:10])

    # Initialize database
    await init_db()
    logger.info("reader_bot_db_initialized")

    # Create bot and dispatcher
    bot = Bot(token=settings.reader_bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Register handlers
    dp.include_router(router)

    # Add database middleware
    dp.update.middleware(db_middleware)

    logger.info("reader_bot_starting_polling")

    # Start polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("reader_bot_stopped_by_user")
    except Exception as e:
        logger.error("reader_bot_error", error=str(e))
        raise
    finally:
        await bot.session.close()
        logger.info("reader_bot_shutdown_complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("reader_bot_interrupted")
        sys.exit(0)
