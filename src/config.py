import os
from dataclasses import dataclass
from typing import Optional, Generator

# Для Pydantic v2 используем отдельный пакет pydantic-settings
# Установите: pip install pydantic-settings
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session


class Settings(BaseSettings):
    # URL подключения к базе данных (PostgreSQL, MySQL и т.п.)
    DB_URL: str
    # Схема по умолчанию (если нужно использовать PostgreSQL schema)
    DB_SCHEMA: str = "public"
    # Флаг режима разработки
    DEV_MODE: bool = False
    # Порт, на котором запускается сервис (если нужен)
    OPERATING_PORT: Optional[int] = None
    # URL брокера для Celery (Redis или RabbitMQ)
    REDIS_URL: Optional[str] = None
    RABBITMQ_URL: Optional[str] = None
    DEFAULT_UA: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Загружаем настройки из .env
settings = Settings()

@dataclass
class DBConfig:
    # Собираем конфиг для работы с БД
    url: str = settings.DB_URL
    schema: str = settings.DB_SCHEMA

# Экземпляр конфига для доступа в приложении
db_config = DBConfig()

# Создаём Engine и SessionLocal
engine = create_engine(
    db_config.url,
    pool_pre_ping=True,
    future=True,
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    class_=Session,
    future=True,
)

# Базовый класс для декларативных моделей
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    Генератор зависимости для FastAPI или других фреймворков.
    Используйте в эндпоинтах так:

        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
