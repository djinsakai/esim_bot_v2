from datetime import datetime
from sqlalchemy import Integer, String, DateTime, BigInteger, Enum, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import enum


class EsimStatus(str, enum.Enum):
    AVAILABLE = "available"
    ISSUED = "issued"
    INVALID = "invalid"


class Base(DeclarativeBase):
    pass


class Esim(Base):
    __tablename__ = "esims"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lpa_string: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(100))
    image_file_id: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default=EsimStatus.AVAILABLE.value)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    issued_to_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
