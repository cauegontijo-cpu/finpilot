"""Configuração centralizada, validada na inicialização.

Todas as configurações vêm de variáveis de ambiente (ou de um arquivo `.env` em
desenvolvimento). Em produção a aplicação recusa iniciar com valores inseguros.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from typing import Any, Self

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRETS = {"", "change-me", "changeme", "secret", "dev", "development"}


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    env: Environment = Environment.DEVELOPMENT
    debug: bool = False
    app_name: str = "FinPilot"
    base_url: str = "http://localhost:8000"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    secret_key: SecretStr = SecretStr("")

    database_url: PostgresDsn
    database_pool_size: int = Field(default=10, ge=1, le=100)
    redis_url: RedisDsn | None = None
    rate_limit_backend: str = Field(default="redis", pattern="^(redis|memory)$")

    # Sessões
    session_cookie_name: str = "fp_session"
    session_idle_timeout_minutes: int = Field(default=30, ge=5, le=24 * 60)
    session_absolute_timeout_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)
    cookie_secure: bool = False

    # Tokens
    email_verification_ttl_hours: int = Field(default=24, ge=1, le=24 * 7)
    password_reset_ttl_minutes: int = Field(default=30, ge=5, le=24 * 60)

    # E-mail
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    mail_from: str = "FinPilot <no-reply@finpilot.local>"
    #: smtp | brevo (API HTTPS — útil onde a porta SMTP é bloqueada) | memory (testes)
    email_backend: str = Field(default="smtp", pattern="^(smtp|brevo|memory)$")
    brevo_api_key: SecretStr | None = None

    # Importação / exportação
    import_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    import_max_rows: int = Field(default=5000, ge=10, le=100_000)
    import_batch_ttl_hours: int = Field(default=24, ge=1, le=24 * 7)
    export_max_rows: int = Field(default=50_000, ge=100, le=1_000_000)

    # Recorrências e previsão
    recurrence_horizon_days: int = Field(default=90, ge=7, le=366)

    # Auditoria
    audit_retention_days: int = Field(default=5 * 365, ge=30)
    audit_auth_failure_retention_days: int = Field(default=180, ge=7)

    # Valores monetários aceitos (em centavos): até R$ 1 bilhão por lançamento
    max_amount_cents: int = 100_000_000_000

    timezone: str = "America/Sao_Paulo"
    log_level: str = "INFO"
    api_docs_enabled: bool = True
    #: Executa recorrências/manutenção numa thread do próprio processo web
    #: (para hospedagens sem cron, ex.: plano gratuito do Render).
    embedded_scheduler: bool = False

    # Demonstração (somente development)
    demo_email: str | None = None
    demo_password: SecretStr | None = None

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self.env is Environment.TESTING

    @property
    def session_cookie_effective_name(self) -> str:
        # Prefixo __Host- exige Secure, Path=/ e ausência de Domain.
        return (
            f"__Host-{self.session_cookie_name}" if self.cookie_secure else self.session_cookie_name
        )

    @model_validator(mode="before")
    @classmethod
    def _platform_defaults(cls, data: Any) -> Any:
        """Na Render, o endereço público vem de RENDER_EXTERNAL_URL/HOSTNAME (sem configuração manual)."""
        if not isinstance(data, dict):
            return data
        external_url = os.environ.get("RENDER_EXTERNAL_URL")
        external_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        keys = {k.lower() for k in data}
        if external_url and "base_url" not in keys and "BASE_URL" not in os.environ:
            data["base_url"] = external_url
        if external_host and "allowed_hosts" not in keys and "ALLOWED_HOSTS" not in os.environ:
            data["allowed_hosts"] = [external_host]
        return data

    @model_validator(mode="after")
    def _validate_security(self) -> Self:
        secret = self.secret_key.get_secret_value()
        if self.is_production:
            problems: list[str] = []
            if secret.lower() in INSECURE_SECRETS or len(secret) < 32:
                problems.append("SECRET_KEY deve ter ao menos 32 caracteres aleatórios")
            if self.debug:
                problems.append("DEBUG não pode estar habilitado em produção")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE deve ser true em produção (HTTPS)")
            if not self.base_url.startswith("https://"):
                problems.append("BASE_URL deve usar https:// em produção")
            if self.rate_limit_backend != "redis" or self.redis_url is None:
                problems.append("Produção exige REDIS_URL e RATE_LIMIT_BACKEND=redis")
            if self.email_backend == "memory":
                problems.append("Produção exige EMAIL_BACKEND=smtp ou brevo")
            if self.email_backend == "brevo" and not self.brevo_api_key:
                problems.append("EMAIL_BACKEND=brevo exige BREVO_API_KEY")
            if not self.allowed_hosts or "*" in self.allowed_hosts:
                problems.append("ALLOWED_HOSTS deve listar explicitamente os hosts válidos")
            if problems:
                raise ValueError("Configuração insegura para produção: " + "; ".join(problems))
        elif len(secret) < 16:
            raise ValueError("SECRET_KEY é obrigatória (mínimo de 16 caracteres fora de produção)")
        if self.rate_limit_backend == "redis" and self.redis_url is None:
            raise ValueError("REDIS_URL é obrigatória quando RATE_LIMIT_BACKEND=redis")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
