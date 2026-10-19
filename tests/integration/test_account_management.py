import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import app
from app.models import AuditLog, Category, User, UserSession
from tests.conftest import TEST_PASSWORD, extract_token, login, post_form


def test_change_name(auth_client: TestClient, user: Any, db: Session) -> None:
    response = post_form(auth_client, "/app/configuracoes/perfil", {"full_name": "Ana Paula Souza"})
    assert response.status_code == 303
    db.refresh(user)
    assert user.full_name == "Ana Paula Souza"


def test_change_password_requires_current_and_revokes_other_sessions(
    client: TestClient, user: Any, db: Session
) -> None:
    other = TestClient(app, base_url="http://testserver")
    from app.core.database import get_db

    assert get_db in app.dependency_overrides
    login(other, user.email)
    login(client, user.email)

    wrong = post_form(
        client,
        "/app/configuracoes/senha",
        {
            "current_password": "errada-errada",
            "password": "Nova-Senha-123-ab",
            "password_confirm": "Nova-Senha-123-ab",
        },
    )
    assert wrong.status_code == 422
    ok = post_form(
        client,
        "/app/configuracoes/senha",
        {
            "current_password": TEST_PASSWORD,
            "password": "Nova-Senha-123-ab",
            "password_confirm": "Nova-Senha-123-ab",
        },
    )
    assert ok.status_code == 303
    active = db.scalar(
        select(func.count())
        .select_from(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    )
    assert active == 1
    assert other.get("/app/configuracoes", follow_redirects=False).status_code == 303
    assert client.get("/app/configuracoes", follow_redirects=False).status_code == 200


def test_change_email_requires_confirmation_on_new_address(
    auth_client: TestClient, user: Any, db: Session
) -> None:
    response = post_form(
        auth_client,
        "/app/configuracoes/email",
        {"email": "ana.nova@example.com", "password": TEST_PASSWORD},
    )
    assert response.status_code == 303
    db.refresh(user)
    assert user.email == "ana@example.com"  # ainda não confirmado
    confirm = next(m for m in app.state.mailer.outbox if m.to == "ana.nova@example.com")
    notice = next(m for m in app.state.mailer.outbox if m.to == "ana@example.com")
    assert "a***@example.com" in notice.text
    token = extract_token(confirm.text, "/confirmar-email")
    assert post_form(auth_client, "/confirmar-email", {"token": token}).status_code == 303
    db.refresh(user)
    assert user.email == "ana.nova@example.com"


def test_change_email_to_existing_address_fails_at_confirmation(
    auth_client: TestClient, user: Any, other_user: Any, db: Session
) -> None:
    post_form(
        auth_client,
        "/app/configuracoes/email",
        {"email": other_user.email, "password": TEST_PASSWORD},
    )
    confirm = next(m for m in app.state.mailer.outbox if m.to == other_user.email)
    token = extract_token(confirm.text, "/confirmar-email")
    assert post_form(auth_client, "/confirmar-email", {"token": token}).status_code == 400
    db.refresh(user)
    assert user.email == "ana@example.com"


def test_export_contains_own_data_without_secrets(
    auth_client: TestClient, user: Any, other_user: Any
) -> None:
    response = post_form(auth_client, "/app/configuracoes/exportar", {})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = json.loads(response.text)
    assert data["usuario"]["email"] == user.email
    assert "password_hash" not in data["usuario"]
    assert len(data["categorias"]) == 14
    assert other_user.email not in response.text
    assert all("token_hash" not in s for s in data["sessoes"])


def test_delete_account_requires_password_and_email_and_cascades(
    auth_client: TestClient, user: Any, other_user: Any, db: Session
) -> None:
    bad = post_form(
        auth_client,
        "/app/configuracoes/excluir-conta",
        {"password": TEST_PASSWORD, "email_confirmation": "outro@x.com"},
    )
    assert bad.status_code == 422
    ok = post_form(
        auth_client,
        "/app/configuracoes/excluir-conta",
        {"password": TEST_PASSWORD, "email_confirmation": user.email},
    )
    assert ok.status_code == 303
    user_id = user.id
    db.expire_all()
    assert db.get(User, user_id) is None
    assert (
        db.scalar(select(func.count()).select_from(Category).where(Category.user_id == user_id))
        == 0
    )
    assert (
        db.scalar(
            select(func.count()).select_from(Category).where(Category.user_id == other_user.id)
        )
        == 14
    )
    # Auditoria permanece pseudonimizada (sem e-mail/nome)
    logs = db.scalars(select(AuditLog).where(AuditLog.user_id == user_id)).all()
    assert any(log.event_type == "user.deleted" for log in logs)
    assert all(user.email not in json.dumps(log.changes or {}) for log in logs)
