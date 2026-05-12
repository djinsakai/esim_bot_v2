import asyncio
import traceback
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from app.utils.config import config
from app.bot.handlers import router
from app.bot.middleware import setup_middleware
from app.db.database import init_db


async def error_handler(event: ErrorEvent):
    print("=" * 50)
    print(f"ERROR CAUGHT!")
    print(f"Update that caused error: {event.update}")
    print(f"Exception type: {type(event.exception).__name__}")
    print(f"Exception message: {event.exception}")
    print("-" * 50)
    print("Traceback:")
    traceback.print_exc()
    print("=" * 50)


async def main():
    bot = Bot(token=config.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Add global error handler
    dp.error.register(error_handler)
    
    setup_middleware(dp)
    dp.include_router(router)
    
    await init_db()
    
    print("Bot started...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
