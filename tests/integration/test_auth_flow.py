from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.main import app
from app.models import AuditLog, Category, User, UserSession
from tests.conftest import TEST_PASSWORD, extract_token, login, post_form


def _register(client: TestClient, email: str = "carla@example.com", **overrides: Any) -> Any:
    data = {
        "full_name": "Carla Mendes",
        "email": email,
        "password": TEST_PASSWORD,
        "password_confirm": TEST_PASSWORD,
        "accept_privacy": "true",
        **overrides,
    }
    return post_form(client, "/cadastro", data)


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


def test_register_creates_user_default_categories_and_sends_verification(
    client: TestClient, db: Session
) -> None:
    response = _register(client)
    assert response.status_code == 200
    user = db.scalars(select(User).where(User.email == "carla@example.com")).one()
    assert user.password_hash.startswith("$argon2id$")
    assert TEST_PASSWORD not in user.password_hash
    assert user.email_verified_at is None
    categories = db.scalars(select(Category).where(Category.user_id == user.id)).all()
    assert len(categories) == 14
    outbox = app.state.mailer.outbox
    assert len(outbox) == 1 and outbox[0].to == "carla@example.com"


def test_register_validation_errors(client: TestClient) -> None:
    response = _register(client, password_confirm="outra-coisa-123", accept_privacy="")
    assert response.status_code == 422
    assert "As senhas não conferem." in response.text
    assert "política de privacidade" in response.text


def test_register_existing_email_does_not_reveal_existence(
    client: TestClient, user: Any, db: Session
) -> None:
    response = _register(client, email=user.email.upper())
    assert response.status_code == 200
    assert "Verifique sua caixa de entrada" in response.text
    assert (
        db.scalars(select(User).where(User.email == user.email)).one().full_name == user.full_name
    )
    assert "Tentativa de cadastro" in app.state.mailer.outbox[-1].subject


def test_email_verification_token_is_single_use(client: TestClient, db: Session) -> None:
    _register(client)
    token = extract_token(app.state.mailer.outbox[0].text, "/verificar-email")
    # GET apenas exibe a confirmação (não consome o token)
    assert client.get(f"/verificar-email?token={token}").status_code == 200
    first = post_form(client, "/verificar-email", {"token": token})
    assert first.status_code == 303
    user = db.scalars(select(User).where(User.email == "carla@example.com")).one()
    assert user.email_verified_at is not None
    second = post_form(client, "/verificar-email", {"token": token})
    assert second.status_code == 400


def test_unverified_user_is_redirected_to_verification(client: TestClient, db: Session) -> None:
    _register(client)
    response = login(client, "carla@example.com")
    assert response.headers["location"] == "/verifique-email"


def test_login_invalid_credentials_generic_message(client: TestClient, user: Any) -> None:
    wrong = post_form(client, "/login", {"email": user.email, "password": "senha-errada-123"})
    missing = post_form(
        client, "/login", {"email": "naoexiste@example.com", "password": "senha-errada-123"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert "E-mail ou senha inválidos." in wrong.text
    assert "E-mail ou senha inválidos." in missing.text


def test_login_rotates_session_and_logout_invalidates(
    client: TestClient, user: Any, db: Session
) -> None:
    cookie_name = get_settings().session_cookie_effective_name
    login(client, user.email)
    first = client.cookies.get(cookie_name)
    login(client, user.email)
    second = client.cookies.get(cookie_name)
    assert first and second and first != second
    sessions = db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all()
    assert sum(1 for s in sessions if s.revoked_at is None) == 1  # sessão anterior revogada

    response = post_form(client, "/logout", {})
    assert response.status_code == 303
    client.cookies.set(cookie_name, second)  # reaproveitar cookie antigo não funciona
    assert client.get("/verifique-email", follow_redirects=False).status_code == 303


def test_login_failures_are_audited_without_email_in_clear(
    client: TestClient, user: Any, db: Session
) -> None:
    post_form(client, "/login", {"email": user.email, "password": "senha-errada-123"})
    log = db.scalars(select(AuditLog).where(AuditLog.event_type == "auth.login_failed")).one()
    assert user.email not in str(log.changes)
    assert log.outcome == "failure"


def test_password_reset_flow_revokes_sessions_and_token_single_use(
    client: TestClient, user: Any, db: Session
) -> None:
    login(client, user.email)
    post_form(client, "/recuperar-senha", {"email": user.email})
    token = extract_token(app.state.mailer.outbox[-1].text, "/redefinir-senha")
    new_password = "Outra-Senha-Segura-99"
    response = post_form(
        client,
        "/redefinir-senha",
        {"token": token, "password": new_password, "password_confirm": new_password},
    )
    assert response.status_code == 303
    active = [
        s
        for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id))
        if s.revoked_at is None
    ]
    assert active == []
    reuse = post_form(
        client,
        "/redefinir-senha",
        {"token": token, "password": new_password, "password_confirm": new_password},
    )
    assert reuse.status_code == 400
    login(client, user.email, new_password)


def test_password_reset_unknown_email_same_response(client: TestClient) -> None:
    response = post_form(client, "/recuperar-senha", {"email": "ninguem@example.com"})
    assert response.status_code == 200
    assert "Se houver uma conta" in response.text
    assert app.state.mailer.outbox == []
