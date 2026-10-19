"""Ambiente do Alembic.

URL do banco: `MIGRATOR_DATABASE_URL` (preferencial, papel com DDL) ou `DATABASE_URL`.
Também aceita `sqlalchemy.url` passada programaticamente (usado nos testes).
"""

from __future__ import annotations

import os

from alembic import context

from sqlalchemy import create_engine, pool

from app.core.database import _sqlalchemy_url
from app.models import Base
from app.utils.env import load_dotenv_if_present

load_dotenv_if_present()
config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("MIGRATOR_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise RuntimeError("Defina MIGRATOR_DATABASE_URL ou DATABASE_URL")
    return _sqlalchemy_url(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
