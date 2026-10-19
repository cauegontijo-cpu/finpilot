import re
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import safe_next_url
from app.models import PasswordResetToken, UserSession
from app.main import app
from tests.conftest import TEST_PASSWORD, csrf_for, extract_token, login, post_form


def test_post_without_csrf_token_is_rejected(client: TestClient, user: Any) -> None:
    client.get("/login")
    response = client.post("/login", data={"email": user.email, "password": TEST_PASSWORD})
    assert response.status_code == 403
    assert "CSRF" in response.text


def test_post_with_wrong_csrf_token_is_rejected(client: TestClient, user: Any) -> None:
    client.get("/login")
    response = client.post(
        "/login", data={"email": user.email, "password": TEST_PASSWORD, "csrf_token": "x" * 64}
    )
    assert response.status_code == 403


def test_csrf_token_from_other_session_is_rejected(client: TestClient, user: Any) -> None:
    login(client, user.email)
    stolen = csrf_for(client)
    login(client, user.email)  # nova sessão → token antigo deixa de valer
    response = client.post("/logout", data={"csrf_token": stolen}, follow_redirects=False)
    assert response.status_code == 403


def test_cross_origin_post_is_rejected_even_with_token(client: TestClient, user: Any) -> None:
    token = csrf_for(client)
    response = client.post(
        "/login",
        data={"email": user.email, "password": TEST_PASSWORD, "csrf_token": token},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_htmx_header_token_is_accepted(client: TestClient, user: Any) -> None:
    token = csrf_for(client)
    response = client.post(
        "/login",
        data={"email": user.email, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": token, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/app"


def test_login_brute_force_is_rate_limited_per_account(client: TestClient, user: Any) -> None:
    for _ in range(5):
        assert post_form(client, "/login", {"email": user.email, "password": "senha-errada-xx"}).status_code == 401
    blocked = post_form(client, "/login", {"email": user.email, "password": TEST_PASSWORD})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_password_reset_is_rate_limited(client: TestClient, user: Any) -> None:
    statuses = [post_form(client, "/recuperar-senha", {"email": user.email}).status_code for _ in range(5)]
    assert statuses[:3] == [200, 200, 200]
    assert 429 in statuses


def test_expired_reset_token_is_rejected(client: TestClient, user: Any, db: Session) -> None:
    post_form(client, "/recuperar-senha", {"email": user.email})
    token = extract_token(app.state.mailer.outbox[-1].text, "/redefinir-senha")
    db.execute(update(PasswordResetToken).values(expires_at=PasswordResetToken.created_at - timedelta(minutes=1)))
    response = post_form(
        client, "/redefinir-senha", {"token": token, "password": "Nova-Senha-Segura-1", "password_confirm": "Nova-Senha-Segura-1"}
    )
    assert response.status_code == 400
    assert db.scalars(select(PasswordResetToken)).one().used_at is None


def test_only_token_hash_is_stored(client: TestClient, user: Any, db: Session) -> None:
    post_form(client, "/recuperar-senha", {"email": user.email})
    token = extract_token(app.state.mailer.outbox[-1].text, "/redefinir-senha")
    stored = db.scalars(select(PasswordResetToken)).one()
    assert stored.token_hash != token and len(stored.token_hash) == 64
    login(client, user.email)
    raw_session = client.cookies.get(get_settings().session_cookie_effective_name)
    assert raw_session
    assert all(s.token_hash != raw_session for s in db.scalars(select(UserSession)))


def test_idle_session_expires(client: TestClient, user: Any, db: Session) -> None:
    login(client, user.email)
    assert client.get("/app/configuracoes", follow_redirects=False).status_code == 200
    idle = get_settings().session_idle_timeout_minutes
    db.execute(update(UserSession).values(last_seen_at=UserSession.last_seen_at - timedelta(minutes=idle + 1)))
    response = client.get("/app/configuracoes", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_absolute_session_expiry(client: TestClient, user: Any, db: Session) -> None:
    login(client, user.email)
    db.execute(update(UserSession).values(expires_at=UserSession.created_at))
    assert client.get("/app/configuracoes", follow_redirects=False).status_code == 303


def test_session_cookie_flags(client: TestClient, user: Any) -> None:
    response = login(client, user.email)
    cookie = next(h for h in response.headers.get_list("set-cookie") if h.startswith(get_settings().session_cookie_effective_name))
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie or "samesite=lax" in cookie.lower()


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Strict-Transport-Security" not in response.headers  # somente produção + HTTPS
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("/app/transacoes", "/app/transacoes"),
        ("https://evil.example", "/app"),
        ("//evil.example", "/app"),
        ("/\\evil.example", "/app"),
        ("javascript:alert(1)", "/app"),
        ("/app\r\nSet-Cookie: x", "/app"),
        (None, "/app"),
    ],
)
def test_open_redirect_prevented(candidate: str | None, expected: str) -> None:
    assert safe_next_url(candidate) == expected


def test_login_next_parameter_cannot_redirect_externally(client: TestClient, user: Any) -> None:
    response = post_form(client, "/login", {"email": user.email, "password": TEST_PASSWORD, "next": "https://evil.example"})
    assert response.headers["location"] == "/app"


def test_errors_do_not_leak_internals(client: TestClient) -> None:
    response = client.get("/rota-inexistente")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    api = client.get("/api/v1/inexistente")
    assert api.headers["content-type"].startswith("application/problem+json")


def _hidden_csrf(html: str) -> str:
    import re  # noqa: PLC0415

    match = re.search(r'name="csrf_token" value="([0-9a-f]{64})"', html)
    assert match, "formulário sem token CSRF renderizado"
    return match.group(1)


def test_rendered_forms_carry_valid_csrf_token(client: TestClient, user: Any) -> None:
    """Fluxo real de navegador: o token vem do HTML, não de cálculo no teste."""
    page = client.get("/login")
    token = _hidden_csrf(page.text)
    meta = re.search(r'name="csrf-token" content="([0-9a-f]{64})"', page.text)
    assert meta and meta.group(1) == token
    response = client.post(
        "/login", data={"email": user.email, "password": TEST_PASSWORD, "csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303
    settings_page = client.get("/app/configuracoes")
    token2 = _hidden_csrf(settings_page.text)
    assert token2 != token  # novo vínculo após login
    assert client.post("/app/configuracoes/perfil", data={"full_name": "Ana S.", "csrf_token": token2}, follow_redirects=False).status_code == 303
