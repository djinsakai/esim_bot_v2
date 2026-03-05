from aiogram.filters import Filter
from aiogram.types import Message
from sqlalchemy import select
from app.utils.config import config
from app.db.models import User
from app.db.database import async_session_maker


class AllowedUserFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        user_id = message.from_user.id
        
        # Check if superadmin
        if user_id in config.allowed_users:
            return True
        
        # Check database
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(User).where(User.telegram_id == user_id)
                )
                user = result.scalar_one_or_none()
                return user is not None
        except Exception:
            return False
