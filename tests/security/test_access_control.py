"""Controle de acesso: IDOR/BOLA, rotas privadas e mass assignment."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import Account
from tests.conftest import post_form
from tests.factories import make_account, make_transfer, make_tx

HX = {"HX-Request": "true"}

PRIVATE_GET_ROUTES = [
    "/app/contas",
    "/app/contas/nova",
    "/app/categorias",
    "/app/transacoes",
    "/app/transacoes/nova",
    "/app/transferencias",
    "/app/configuracoes",
]


@pytest.mark.parametrize("url", PRIVATE_GET_ROUTES)
def test_private_routes_require_authentication(client: TestClient, url: str) -> None:
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")


def test_all_app_routes_are_protected() -> None:
    """Varre o roteador: toda rota /app ou /api (exceto auth pública) depende de sessão."""
    from fastapi.routing import APIRoute

    public_api = {
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/csrf",
        "/api/v1/auth/password-reset",
    }
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(("/app", "/api/v1")) or route.path in public_api:
            continue
        names = {d.call.__name__ for d in _flatten(route.dependant)}
        assert names & {"require_session", "require_verified_session"}, route.path


def _flatten(dependant: Any) -> list[Any]:
    out = []
    for d in dependant.dependencies:
        if d.call is not None:
            out.append(d)
        out.extend(_flatten(d))
    return out


def test_cannot_view_or_edit_other_users_account(
    auth_client: TestClient, db: Session, other_user: Any
) -> None:
    theirs = make_account(db, other_user, name="Conta do Bruno", initial=50_000)
    assert auth_client.get(f"/app/contas/{theirs.id}/editar").status_code == 404
    response = post_form(
        auth_client,
        f"/app/contas/{theirs.id}",
        {
            "name": "Hackeada",
            "type": "checking",
            "initial_balance": "999.999,00",
            "start_date": "2026-01-01",
        },
        headers=HX,
    )
    assert response.status_code == 404
    db.refresh(theirs)
    assert theirs.name == "Conta do Bruno" and theirs.current_balance_cents == 50_000
    for action in ("arquivar", "excluir", "reativar"):
        assert (
            post_form(auth_client, f"/app/contas/{theirs.id}/{action}", {}, headers=HX).status_code
            == 404
        )


def test_cannot_touch_other_users_transactions_and_transfers(
    auth_client: TestClient, db: Session, other_user: Any
) -> None:
    a = make_account(db, other_user, name="A", initial=50_000)
    b = make_account(db, other_user, name="B", initial=0)
    tx = make_tx(db, other_user, a, 1_000)
    transfer = make_transfer(db, other_user, a, b, 1_000)
    assert auth_client.get(f"/app/transacoes/{tx.id}/editar").status_code == 404
    assert auth_client.get(f"/app/transacoes/{tx.id}/historico").status_code == 404
    assert (
        post_form(auth_client, f"/app/transacoes/{tx.id}/cancelar", {}, headers=HX).status_code
        == 404
    )
    assert (
        post_form(
            auth_client, f"/app/transferencias/{transfer.id}/cancelar", {}, headers=HX
        ).status_code
        == 404
    )
    listing = auth_client.get("/app/transacoes?period=all")
    assert "Lançamento de teste" not in listing.text


def test_cannot_create_transaction_into_other_users_account(
    auth_client: TestClient, db: Session, user: Any, other_user: Any
) -> None:
    theirs = make_account(db, other_user, name="Bruno", initial=0)
    response = post_form(
        auth_client,
        "/app/transacoes",
        {
            "type": "income",
            "description": "x",
            "amount": "10,00",
            "account_id": str(theirs.id),
            "status": "settled",
            "competence_date": "2026-10-01",
            "idempotency_key": "k" * 20,
        },
        headers=HX,
    )
    assert response.status_code in (404, 422)
    db.refresh(theirs)
    assert theirs.current_balance_cents == 0


def test_mass_assignment_fields_are_ignored(
    auth_client: TestClient, db: Session, user: Any, other_user: Any
) -> None:
    response = post_form(
        auth_client,
        "/app/contas",
        {
            "name": "Minha",
            "type": "checking",
            "initial_balance": "10,00",
            "start_date": "2026-01-01",
            "user_id": str(other_user.id),
            "current_balance_cents": "999999999",
            "color": "#2563eb",
            "icon": "wallet",
        },
        headers=HX,
    )
    assert response.status_code == 204
    account = db.query(Account).filter(Account.name == "Minha").one()
    assert account.user_id == user.id
    assert account.current_balance_cents == 1_000


@pytest.mark.parametrize(
    "payload",
    [
        "' OR 1=1 --",
        "'; DROP TABLE transactions; --",
        "%' UNION SELECT password_hash FROM users --",
    ],
)
def test_sql_injection_in_search_is_harmless(
    auth_client: TestClient, db: Session, user: Any, other_user: Any, payload: str
) -> None:
    a = make_account(db, other_user, name="Outra", initial=10_000)
    make_tx(db, other_user, a, 100, description="Segredo do Bruno")
    response = auth_client.get("/app/transacoes", params={"q": payload, "period": "all"})
    assert response.status_code == 200
    assert "Segredo do Bruno" not in response.text
    assert "$argon2" not in response.text


def test_invalid_uuid_is_404_not_500(auth_client: TestClient) -> None:
    assert auth_client.get("/app/contas/nao-e-um-uuid/editar").status_code == 404
