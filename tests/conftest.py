"""Fixtures compartilhadas.

* Banco PostgreSQL REAL (serviço `db-test` do docker-compose), esquema criado pelas
  migrations do Alembic — as migrations também são testadas.
* Isolamento: cada teste roda dentro de uma transação externa revertida ao final
  (os `commit()` dos serviços viram SAVEPOINTs).
* Testes de concorrência usam conexões reais e limpam as tabelas ao final.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest

os.environ.update(
    {
        "ENV": "testing",
        "SECRET_KEY": "test-secret-key-0123456789abcdef",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+psycopg://finpilot_test:finpilot_test@127.0.0.1:5433/finpilot_test",
        ),
        "RATE_LIMIT_BACKEND": "memory",
        "EMAIL_BACKEND": "memory",
        "BASE_URL": "http://testserver",
        "COOKIE_SECURE": "false",
        "LOG_LEVEL": "WARNING",
    }
)
os.environ.pop("REDIS_URL", None)

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from app.core.clock import Clock, FrozenClock
from app.core.config import get_settings
from app.core.database import get_db, get_engine
from app.core.mailer import MemoryMailer
from app.core.ratelimit import MemoryRateLimiter
from app.core.security import csrf_token_for
from app.main import app
from app.services.audit import RequestMeta

TEST_PASSWORD = "Senha-Forte-Teste-2026"


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Any]:
    url = os.environ["DATABASE_URL"]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("DROP FUNCTION IF EXISTS audit_logs_block_changes() CASCADE"))
    admin.dispose()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = get_engine()
    yield engine
    engine.dispose()


@pytest.fixture
def connection(migrated_engine: Any) -> Iterator[Connection]:
    conn = migrated_engine.connect()
    outer = conn.begin()
    yield conn
    outer.rollback()
    conn.close()


@pytest.fixture
def db(connection: Connection) -> Iterator[Session]:
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )
    yield session
    session.close()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 10, 15, 15, 0, tzinfo=UTC))


@pytest.fixture
def mailer() -> MemoryMailer:
    return app.state.mailer  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
def _reset_app_state() -> Iterator[None]:
    app.state.rate_limiter = MemoryRateLimiter()
    app.state.mailer = MemoryMailer()
    app.state.clock = Clock()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    def _override() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def csrf_for(client: TestClient) -> str:
    """Token CSRF correspondente aos cookies atuais do cliente."""
    settings = get_settings()
    raw = client.cookies.get(settings.session_cookie_effective_name)
    if raw:
        binding = f"s:{raw}"
    else:
        anon = client.cookies.get("fp_anon")
        if anon is None:
            client.get("/login")
            anon = client.cookies.get("fp_anon")
        binding = f"a:{anon}"
    return csrf_token_for(settings.secret_key.get_secret_value(), binding)


def post_form(client: TestClient, url: str, data: dict[str, Any], **kwargs: Any) -> Any:
    payload = {"csrf_token": csrf_for(client), **data}
    return client.post(
        url, data=payload, follow_redirects=kwargs.pop("follow_redirects", False), **kwargs
    )


def api(client: TestClient, method: str, url: str, **kwargs: Any) -> Any:
    headers = kwargs.pop("headers", {})
    if method.upper() not in {"GET", "HEAD"}:
        headers.setdefault("X-CSRF-Token", csrf_for(client))
    return client.request(method, url, headers=headers, **kwargs)


def extract_token(text_body: str, path: str) -> str:
    match = re.search(re.escape(path) + r"\?token=([A-Za-z0-9_-]+)", text_body)
    assert match, f"token não encontrado para {path}"
    return match.group(1)


# ---------------------------------------------------------------- usuários
def make_user(
    db: Session, *, email: str = "ana@example.com", name: str = "Ana Souza", verified: bool = True
) -> Any:
    from app.services.auth import AuthService

    service = AuthService(
        db,
        get_settings(),
        Clock(),
        MemoryRateLimiter(),
        MemoryMailer(),
        RequestMeta(ip="127.0.0.1"),
    )
    service.register(
        full_name=name,
        email=email,
        password=TEST_PASSWORD,
        password_confirm=TEST_PASSWORD,
        accept_privacy=True,
    )
    from app.models import User

    user = db.query(User).filter(User.email == email).one()
    if verified:
        user.email_verified_at = datetime.now(UTC)
        db.commit()
    return user


@pytest.fixture
def user(db: Session) -> Any:
    return make_user(db)


@pytest.fixture
def other_user(db: Session) -> Any:
    return make_user(db, email="bruno@example.com", name="Bruno Lima")


def login(client: TestClient, email: str, password: str = TEST_PASSWORD) -> Any:
    response = post_form(client, "/login", {"email": email, "password": password})
    assert response.status_code == 303, response.text
    return response


@pytest.fixture
def auth_client(client: TestClient, user: Any) -> TestClient:
    login(client, user.email)
    return client


TODAY = date(2026, 10, 15)
