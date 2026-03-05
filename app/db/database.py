from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.utils.config import config
from app.db.models import Base


engine = create_async_engine(config.async_database_url, echo=False)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
