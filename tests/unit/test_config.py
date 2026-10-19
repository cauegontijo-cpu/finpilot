"""Configuração validada na inicialização: produção recusa valores inseguros (seção 18.6)."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings

SAFE_PROD: dict[str, Any] = {
    "env": "production",
    "secret_key": "x" * 48,
    "database_url": "postgresql+psycopg://u:p@db:5432/finpilot",
    "redis_url": "redis://redis:6379/0",
    "rate_limit_backend": "redis",
    "email_backend": "smtp",
    "cookie_secure": True,
    "base_url": "https://finpilot.example.com",
    "allowed_hosts": ["finpilot.example.com"],
    "debug": False,
}


def build(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**SAFE_PROD, **overrides})  # type: ignore[call-arg]


def test_safe_production_config_is_accepted() -> None:
    settings = build()
    assert settings.is_production
    assert settings.session_cookie_effective_name == "__Host-fp_session"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"secret_key": "change-me"}, "SECRET_KEY"),
        ({"secret_key": "curta"}, "SECRET_KEY"),
        ({"debug": True}, "DEBUG"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"base_url": "http://finpilot.example.com"}, "https"),
        ({"rate_limit_backend": "memory"}, "REDIS"),
        ({"email_backend": "memory"}, "EMAIL_BACKEND"),
        ({"allowed_hosts": ["*"]}, "ALLOWED_HOSTS"),
    ],
)
def test_insecure_production_config_is_rejected(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        build(**override)


def test_development_requires_secret_and_redis_when_selected() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(
            _env_file=None,
            env="development",
            secret_key="",
            database_url=SAFE_PROD["database_url"],
            rate_limit_backend="memory",
        )  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="REDIS_URL"):
        Settings(
            _env_file=None,
            env="development",
            secret_key="s" * 20,
            database_url=SAFE_PROD["database_url"],
            rate_limit_backend="redis",
            redis_url=None,
        )  # type: ignore[call-arg]
