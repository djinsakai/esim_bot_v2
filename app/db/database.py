from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.utils.config import config
from app.db.models import Base


# Use NullPool to avoid connection leaks, recreate connections each time
engine = create_async_engine(
    config.async_database_url, 
    echo=False,
    poolclass=NullPool,  # No connection pooling - creates fresh connection each time
)

async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncSession:
    async with async_session_maker() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
