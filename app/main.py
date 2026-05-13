import asyncio
import socket
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession

from app.utils.config import config
from app.bot.handlers import router
from app.bot.middleware import setup_middleware
from app.db.database import init_db


# Кастомный класс сессии для решения проблем с IPv6 и таймаутами
class NoIPv6Session(AiohttpSession):
    async def create_session(self) -> aiohttp.ClientSession:
        # Проверяем, существует ли сессия, чтобы не создавать её на каждый запрос
        if self._session is None or self._session.closed:
            # Принудительно используем только IPv4
            connector = aiohttp.TCPConnector(
                family=socket.AF_INET,
                limit=100,
                ttl_dns_cache=300
            )

            # Конвертируем float таймаут (от aiogram) в объект ClientTimeout (для aiohttp)
            client_timeout = aiohttp.ClientTimeout(total=self.timeout)

            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=client_timeout
            )
        return self._session


async def main():
    # Инициализируем нашу кастомную сессию (timeout передается как float!)
    session = NoIPv6Session(timeout=60.0)

    # Передаем сессию боту
    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher(storage=MemoryStorage())

    setup_middleware(dp)
    dp.include_router(router)

    # Инициализация базы данных
    await init_db()

    print("Bot started...")
    try:
        # Запускаем поллинг
        await dp.start_polling(bot)
    finally:
        # Корректно закрываем сессии при остановке бота
        await session.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())