from aiogram import Dispatcher
from aiogram.types import Update, Message, CallbackQuery
from aiogram import BaseMiddleware
from sqlalchemy import select
from app.utils.config import config
from app.db.models import User
from app.db.database import async_session_maker
import logging

logger = logging.getLogger(__name__)


# DEBUG: Print ALL updates before anything else
class DebugMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery):
            print(f"DEBUG: Received CallbackQuery from {event.from_user.id}, data: {event.data}")
        elif isinstance(event, Message):
            print(f"DEBUG: Received Message from {event.from_user.id}, text: {event.text[:50] if event.text else 'None'}")
        return await handler(event, data)


class AllowedUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = None
        
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            print(f"MIDDLEWARE: Callback query from user {user_id}, data: {event.data}")
        else:
            return await handler(event, data)
        
        logger.info(f"Checking authorization for user_id: {user_id}")
        
        # Check if superadmin (in ALLOWED_USERS)
        if user_id in config.allowed_users:
            logger.info(f"User {user_id} is superadmin")
            return await handler(event, data)
        
        # Check database for regular users
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(User).where(User.telegram_id == user_id)
                )
                user = result.scalar_one_or_none()
                
                if user:
                    logger.info(f"User {user_id} found in database")
                    return await handler(event, data)
                else:
                    logger.info(f"User {user_id} not found in database")
        except Exception as e:
            logger.error(f"Database error: {e}")
        
        # Not authorized
        logger.info(f"User {user_id} is not authorized")
        if isinstance(event, Message):
            await event.answer("⛔️ У вас нет доступа к боту.")
        elif isinstance(event, CallbackQuery):
            await event.answer("⛔️ У вас нет доступа к боту.", show_alert=True)


def setup_middleware(dp: Dispatcher):
    # Debug middleware first to see all updates
    dp.message.middleware(DebugMiddleware())
    dp.callback_query.middleware(DebugMiddleware())
    
    # Auth middleware
    dp.message.middleware(AllowedUserMiddleware())
    dp.callback_query.middleware(AllowedUserMiddleware())
