from dataclasses import dataclass
from dotenv import load_dotenv
import os


@dataclass
class Config:
    bot_token: str
    allowed_users: list[int]
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    database_url: str
    
    @property
    def async_database_url(self) -> str:
        if self.database_url:
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://")
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
    
    @property
    def sync_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"


def load_config() -> Config:
    load_dotenv()
    
    allowed_users_str = os.getenv("ALLOWED_USERS", "")
    allowed_users = [int(u.strip()) for u in allowed_users_str.split(",") if u.strip()]
    
    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        allowed_users=allowed_users,
        db_host=os.getenv("DB_HOST", "localhost"),
        db_port=int(os.getenv("DB_PORT", 5432)),
        db_name=os.getenv("DB_NAME", "esim_bot"),
        db_user=os.getenv("DB_USER", "postgres"),
        db_password=os.getenv("DB_PASSWORD", ""),
        database_url=os.getenv("DATABASE_URL", ""),
    )


config = load_config()
