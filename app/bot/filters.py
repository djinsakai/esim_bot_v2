from aiogram.filters import Filter
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from app.utils.config import config
from app.db.models import User
from app.db.database import async_session_maker


class AllowedUserFilter(Filter):
    async def __call__(self, event) -> bool:
        # Handle both Message and CallbackQuery
        user_id = event.from_user.id
        print(f"AllowedUserFilter check for user_id: {user_id}")
        
        # Check if superadmin (always allowed)
        if user_id in config.allowed_users:
            print(f"User {user_id} is superadmin - ALLOWED")
            return True
        
        # Check database for regular users
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(User).where(User.telegram_id == user_id)
                )
                user = result.scalar_one_or_none()
                print(f"User {user_id} in database: {user is not None}")
                return user is not None
        except Exception as e:
            print(f"AllowedUserFilter database error: {e}")
            # On database error, allow superadmins but deny others
            return False
