import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.utils.config import config
from app.bot.handlers import router
from app.bot.middleware import setup_middleware
from app.db.database import init_db


async def main():
    bot = Bot(token=config.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    
    setup_middleware(dp)
    dp.include_router(router)
    
    await init_db()
    
    print("Bot started...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
