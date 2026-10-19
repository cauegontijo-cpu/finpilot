"""Integração tela → rota → serviço → banco das telas do núcleo financeiro."""

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.ledger import TransactionStatus
from app.models import Account, Transaction, Transfer
from tests.conftest import post_form
from tests.factories import category, make_account, make_tx

HX = {"HX-Request": "true"}


def _account_form(**overrides: Any) -> dict[str, str]:
    return {
        "name": "Conta Itaú",
        "type": "checking",
        "initial_balance": "1.500,00",
        "start_date": "2026-01-01",
        "color": "#2563eb",
        "icon": "wallet",
        **overrides,
    }


def test_pages_render(auth_client: TestClient) -> None:
    for url in [
        "/app/contas",
        "/app/categorias",
        "/app/transacoes",
        "/app/transferencias",
        "/app/contas/nova",
        "/app/transacoes/nova",
    ]:
        response = auth_client.get(url)
        assert response.status_code == 200, url
        assert "<html" in response.text


def test_create_account_via_htmx_form(auth_client: TestClient, user: Any, db: Session) -> None:
    response = post_form(auth_client, "/app/contas", _account_form(), headers=HX)
    assert response.status_code == 204
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["toast"]["kind"] == "success" and trigger["refresh"] is True
    account = db.scalars(select(Account).where(Account.user_id == user.id)).one()
    assert account.current_balance_cents == 150_000


def test_create_account_invalid_rerenders_form_with_errors(auth_client: TestClient) -> None:
    response = post_form(
        auth_client, "/app/contas", _account_form(name="", initial_balance="abc"), headers=HX
    )
    assert response.status_code == 422
    assert "Valor inválido" in response.text
    assert 'aria-invalid="true"' in response.text


def test_create_transaction_via_form_and_list(
    auth_client: TestClient, user: Any, db: Session
) -> None:
    account = make_account(db, user, initial=100_000)
    food = category(db, user, "Alimentação")
    key = uuid.uuid4().hex
    data = {
        "type": "expense",
        "description": "Feira",
        "amount": "123,45",
        "account_id": str(account.id),
        "expense_category_id": str(food.id),
        "status": "settled",
        "competence_date": "2026-10-10",
        "settlement_date": "2026-10-10",
        "idempotency_key": key,
    }
    first = post_form(auth_client, "/app/transacoes", data, headers=HX)
    assert first.status_code == 204, first.text
    # Reenvio do mesmo formulário (duplo clique) não duplica
    assert post_form(auth_client, "/app/transacoes", data, headers=HX).status_code == 204
    db.refresh(account)
    assert account.current_balance_cents == 100_000 - 12_345
    listing = auth_client.get("/app/transacoes?month=2026-10")
    assert "Feira" in listing.text and "R$ 123,45" in listing.text


def test_transaction_form_requires_category_and_positive_amount(
    auth_client: TestClient, user: Any, db: Session
) -> None:
    account = make_account(db, user)
    data = {
        "type": "expense",
        "description": "X",
        "amount": "0",
        "account_id": str(account.id),
        "status": "settled",
        "competence_date": "2026-10-10",
        "idempotency_key": uuid.uuid4().hex,
    }
    response = post_form(auth_client, "/app/transacoes", data, headers=HX)
    assert response.status_code == 422
    assert "maior que zero" in response.text


def test_cancel_via_web_reverts_balance(auth_client: TestClient, user: Any, db: Session) -> None:
    account = make_account(db, user, initial=10_000)
    tx = make_tx(db, user, account, 2_000)
    assert (
        post_form(auth_client, f"/app/transacoes/{tx.id}/cancelar", {}, headers=HX).status_code
        == 204
    )
    db.refresh(account)
    assert account.current_balance_cents == 10_000
    assert db.get(Transaction, tx.id).status is TransactionStatus.CANCELLED
    history = auth_client.get(f"/app/transacoes/{tx.id}/historico", headers=HX)
    assert "Estornado" in history.text


def test_transfer_via_web(auth_client: TestClient, user: Any, db: Session) -> None:
    a = make_account(db, user, name="A", initial=10_000)
    b = make_account(db, user, name="B", initial=0)
    data = {
        "from_account_id": str(a.id),
        "to_account_id": str(b.id),
        "amount": "50,00",
        "transfer_date": "2026-10-10",
        "status": "settled",
        "idempotency_key": uuid.uuid4().hex,
    }
    assert post_form(auth_client, "/app/transferencias", data, headers=HX).status_code == 204
    db.refresh(a)
    db.refresh(b)
    assert (a.current_balance_cents, b.current_balance_cents) == (5_000, 5_000)
    assert db.scalars(select(Transfer)).one().amount_cents == 5_000
    same = {**data, "to_account_id": str(a.id), "idempotency_key": uuid.uuid4().hex}
    response = post_form(auth_client, "/app/transferencias", same, headers=HX)
    assert response.status_code == 422 and "diferentes" in response.text


def test_business_error_on_simple_action_returns_toast(
    auth_client: TestClient, user: Any, db: Session
) -> None:
    account = make_account(db, user)
    make_tx(db, user, account, 100)
    response = post_form(auth_client, f"/app/contas/{account.id}/excluir", {}, headers=HX)
    assert response.status_code == 422
    assert "histórico" in json.loads(response.headers["HX-Trigger"])["toast"]["message"]


def test_html_is_escaped_in_listing(auth_client: TestClient, user: Any, db: Session) -> None:
    account = make_account(db, user, initial=100_000, clock=None)
    make_tx(
        db,
        user,
        account,
        100,
        description='<script>alert("x")</script><img src=x onerror=alert(1)>',
    )
    body = auth_client.get("/app/transacoes?month=2026-10").text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body
    assert "<img src=x" not in body
