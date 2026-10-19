"""Engine e sessões do SQLAlchemy (síncrono, psycopg 3)."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _sqlalchemy_url(url: str) -> str:
    # Garante o driver psycopg 3 independentemente do esquema informado.
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        _sqlalchemy_url(str(settings.database_url)),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"options": "-c timezone=UTC -c statement_timeout=15000"},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """Dependência FastAPI: uma sessão por requisição.

    Os serviços controlam explicitamente `commit()`; qualquer exceção não tratada
    resulta em rollback ao fechar a sessão.
    """
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
