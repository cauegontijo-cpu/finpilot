"""Ajustes para hospedagem pública: endereço da Render, e-mail via Brevo e health check."""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.mailer import BrevoMailer, OutgoingEmail, build_mailer
from app.core.middleware import HealthAwareTrustedHostMiddleware

PROD: dict[str, Any] = {
    "env": "production",
    "secret_key": "x" * 48,
    "database_url": "postgresql://u:p@db:5432/finpilot",
    "redis_url": "redis://redis:6379/0",
    "rate_limit_backend": "redis",
    "cookie_secure": True,
    "email_backend": "brevo",
    "brevo_api_key": "chave-de-teste",
    "mail_from": "FinPilot <contato@example.com>",
}


def test_render_external_url_fills_base_url_and_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://finpilot.onrender.com")
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "finpilot.onrender.com")
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    settings = Settings(_env_file=None, **PROD)  # type: ignore[call-arg]
    assert settings.base_url == "https://finpilot.onrender.com"
    assert settings.allowed_hosts == ["finpilot.onrender.com"]


def test_brevo_requires_api_key_in_production() -> None:
    with pytest.raises(ValidationError, match="BREVO_API_KEY"):
        Settings(
            _env_file=None,
            base_url="https://x.example",
            allowed_hosts=["x.example"],
            **{**PROD, "brevo_api_key": None},
        )  # type: ignore[call-arg]


def test_brevo_mailer_posts_to_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    settings = Settings(
        _env_file=None, base_url="https://x.example", allowed_hosts=["x.example"], **PROD
    )  # type: ignore[call-arg]
    mailer = build_mailer(settings)
    assert isinstance(mailer, BrevoMailer)
    mailer.send(
        OutgoingEmail(to="ana@example.com", subject="Olá", text="texto", html="<p>html</p>")
    )
    assert captured["url"] == "https://api.brevo.com/v3/smtp/email"
    assert captured["headers"]["Api-key"] == "chave-de-teste"
    assert captured["body"]["sender"] == {"email": "contato@example.com", "name": "FinPilot"}
    assert captured["body"]["to"] == [{"email": "ana@example.com"}]


def test_health_paths_skip_trusted_host_but_others_are_checked() -> None:
    inner = Starlette(
        routes=[
            Route("/health/live", lambda r: PlainTextResponse("ok")),
            Route("/", lambda r: PlainTextResponse("home")),
        ]
    )
    app = HealthAwareTrustedHostMiddleware(inner, allowed_hosts=["finpilot.onrender.com"])
    client = TestClient(app, base_url="http://10.0.0.5")
    assert client.get("/health/live").status_code == 200
    assert client.get("/").status_code == 400
    assert TestClient(app, base_url="https://finpilot.onrender.com").get("/").status_code == 200
