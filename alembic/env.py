import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# 1) загрузка .env
from dotenv import load_dotenv
load_dotenv()

# 2) подключение метаданных
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  # чтобы импортировать config.py
from src.config import settings, Base  # Base — declarative_base() из config.py
import src.models

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# если используем переменные с ${DB_URL} в alembic.ini
config.set_main_option('sqlalchemy.url', settings.DB_URL)

# Interpret the config file for Python logging.
fileConfig(config.config_file_name)

# Подключаем метаданные для автогенерации
target_metadata = Base.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
