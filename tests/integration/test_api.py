"""API REST v1: contratos, status HTTP, erros padronizados, CSRF e idempotência."""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from tests.conftest import TEST_PASSWORD, api, extract_token
from tests.factories import category, make_account


def _login(client: TestClient, email: str) -> None:
    response = api(
        client, "POST", "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text


def test_register_verify_login_flow_via_api(client: TestClient) -> None:
    payload = {
        "full_name": "Diego Alves",
        "email": "diego@example.com",
        "password": TEST_PASSWORD,
        "password_confirm": TEST_PASSWORD,
        "accept_privacy": True,
    }
    assert api(client, "POST", "/api/v1/auth/register", json=payload).status_code == 202
    token = extract_token(app.state.mailer.outbox[-1].text, "/verificar-email")
    assert (
        api(client, "POST", "/api/v1/auth/verify-email", json={"token": token}).status_code == 204
    )
    _login(client, "diego@example.com")
    me = client.get("/api/v1/users/me").json()
    assert me["email"] == "diego@example.com"
    assert "password_hash" not in me


def test_unauthenticated_api_returns_problem_json(client: TestClient) -> None:
    response = client.get("/api/v1/accounts")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 401 and body["request_id"]


def test_invalid_credentials_api(client: TestClient, user: Any) -> None:
    response = api(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": user.email, "password": "errada-errada"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_api_mutation_requires_csrf_header(client: TestClient, user: Any) -> None:
    _login(client, user.email)
    response = client.post(
        "/api/v1/accounts", json={"name": "X", "type": "checking", "start_date": "2026-01-01"}
    )
    assert response.status_code == 403


def test_account_crud_and_unknown_fields_rejected(client: TestClient, user: Any) -> None:
    _login(client, user.email)
    data = {
        "name": "API Conta",
        "type": "checking",
        "initial_balance_cents": 10_000,
        "start_date": "2026-01-01",
    }
    created = api(client, "POST", "/api/v1/accounts", json=data)
    assert created.status_code == 201
    assert created.headers["Location"].endswith(created.json()["id"])
    assert created.json()["current_balance"] == "100.00"
    extra = api(
        client,
        "POST",
        "/api/v1/accounts",
        json={**data, "name": "Outra", "user_id": str(uuid.uuid4())},
    )
    assert extra.status_code == 422  # mass assignment bloqueado
    assert extra.json()["errors"]


def test_transaction_requires_idempotency_key_and_replays(
    client: TestClient, user: Any, db: Session
) -> None:
    _login(client, user.email)
    account = make_account(db, user, initial=100_000)
    payload = {
        "type": "expense",
        "account_id": str(account.id),
        "category_id": str(category(db, user, "Alimentação").id),
        "description": "API mercado",
        "amount_cents": 2_500,
        "competence_date": "2026-10-10",
        "settlement_date": "2026-10-10",
    }
    missing = api(client, "POST", "/api/v1/transactions", json=payload)
    assert missing.status_code == 422
    headers = {"Idempotency-Key": "api-key-000000001"}
    first = api(client, "POST", "/api/v1/transactions", json=payload, headers=dict(headers))
    second = api(client, "POST", "/api/v1/transactions", json=payload, headers=dict(headers))
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["amount"] == "25.00"
    conflict = api(
        client,
        "POST",
        "/api/v1/transactions",
        json={**payload, "amount_cents": 9_999},
        headers=dict(headers),
    )
    assert conflict.status_code == 409
    db.refresh(account)
    assert account.current_balance_cents == 97_500


def test_transaction_list_pagination_sort_and_invalid_sort(
    client: TestClient, user: Any, db: Session
) -> None:
    _login(client, user.email)
    account = make_account(db, user, initial=100_000)
    cat = category(db, user, "Alimentação")
    for i in range(3):
        api(
            client,
            "POST",
            "/api/v1/transactions",
            json={
                "type": "expense",
                "account_id": str(account.id),
                "category_id": str(cat.id),
                "description": f"Item {i}",
                "amount_cents": 100 * (i + 1),
                "competence_date": "2026-10-10",
                "settlement_date": "2026-10-10",
            },
            headers={"Idempotency-Key": f"list-key-{i:010d}"},
        )
    page = client.get("/api/v1/transactions", params={"page_size": 2, "sort": "-amount"}).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert [t["amount_cents"] for t in page["items"]] == [300, 200]
    bad = client.get("/api/v1/transactions", params={"sort": "user_id"})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_sort"


def test_business_rule_violation_status(client: TestClient, user: Any, db: Session) -> None:
    _login(client, user.email)
    account = make_account(db, user, initial=100)
    response = api(
        client,
        "POST",
        "/api/v1/transactions",
        json={
            "type": "expense",
            "account_id": str(account.id),
            "category_id": str(category(db, user, "Lazer").id),
            "description": "Caro",
            "amount_cents": 5_000,
            "competence_date": "2026-10-10",
            "settlement_date": "2026-10-10",
        },
        headers={"Idempotency-Key": "insufficient-0001"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "RN008"


def test_other_users_resources_are_404_in_api(
    client: TestClient, user: Any, other_user: Any, db: Session
) -> None:
    _login(client, user.email)
    theirs = make_account(db, other_user, name="Bruno", initial=10_000)
    assert client.get(f"/api/v1/accounts/{theirs.id}").status_code == 404
    assert api(client, "DELETE", f"/api/v1/accounts/{theirs.id}").status_code == 404
    assert (
        client.get(
            "/api/v1/dashboard", params={"year": 2026, "month": 10, "account_id": str(theirs.id)}
        ).status_code
        == 404
    )


def test_transfer_budget_and_dashboard_endpoints(
    client: TestClient, user: Any, db: Session
) -> None:
    _login(client, user.email)
    a = make_account(db, user, name="A", initial=50_000)
    b = make_account(db, user, name="B", initial=0)
    transfer = api(
        client,
        "POST",
        "/api/v1/transfers",
        json={
            "from_account_id": str(a.id),
            "to_account_id": str(b.id),
            "amount_cents": 10_000,
            "transfer_date": "2026-10-01",
        },
        headers={"Idempotency-Key": "transfer-api-0001"},
    )
    assert transfer.status_code == 201
    budget = api(
        client,
        "POST",
        "/api/v1/budgets",
        json={
            "category_id": str(category(db, user, "Lazer").id),
            "year": 2026,
            "month": 10,
            "limit_cents": 30_000,
        },
    )
    assert budget.status_code == 201 and budget.json()["level"] == "ok"
    dash = client.get("/api/v1/dashboard", params={"year": 2026, "month": 10})
    assert dash.status_code == 200
    body = dash.json()
    assert (
        body["balance_cents"] == 50_000 and body["current"]["income_cents"] == 0
    )  # transferência não é receita
    forecast = client.get("/api/v1/dashboard/forecast", params={"days": 60})
    assert forecast.status_code == 200 and len(forecast.json()["series"]) == 61
    cancel = api(client, "POST", f"/api/v1/transfers/{transfer.json()['id']}/cancel")
    assert cancel.json()["status"] == "cancelled"


def test_openapi_documentation_available(client: TestClient) -> None:
    schema = client.get("/api/openapi.json").json()
    paths = schema["paths"]
    for path in [
        "/api/v1/accounts",
        "/api/v1/transactions",
        "/api/v1/transfers",
        "/api/v1/budgets",
        "/api/v1/dashboard",
    ]:
        assert path in paths
    assert not any(p.startswith("/app") for p in paths)  # páginas HTML fora da documentação


def test_planning_cards_imports_reports_api(client: TestClient, user: Any, db: Session) -> None:
    _login(client, user.email)
    account = make_account(db, user, initial=500_000)
    goal = api(client, "POST", "/api/v1/goals", json={"name": "Viagem", "target_cents": 100_000})
    assert goal.status_code == 201
    alloc = api(
        client,
        "POST",
        f"/api/v1/goals/{goal.json()['id']}/allocations",
        json={"amount_cents": 25_000},
    )
    assert alloc.status_code == 201
    assert client.get(f"/api/v1/goals/{goal.json()['id']}").json()["allocated_cents"] == 25_000

    rec = api(
        client,
        "POST",
        "/api/v1/recurrences",
        json={
            "type": "expense",
            "account_id": str(account.id),
            "category_id": str(category(db, user, "Moradia").id),
            "description": "Aluguel",
            "amount_cents": 150_000,
            "frequency": "monthly",
            "start_date": "2026-11-10",
        },
    )
    assert rec.status_code == 201
    assert (
        len(client.get(f"/api/v1/recurrences/{rec.json()['id']}/next", params={"count": 3}).json())
        == 3
    )
    assert (
        api(client, "POST", f"/api/v1/recurrences/{rec.json()['id']}/pause").json()["status"]
        == "paused"
    )

    card = api(
        client,
        "POST",
        "/api/v1/cards",
        json={
            "name": "Visa",
            "limit_cents": 300_000,
            "closing_day": 25,
            "due_day": 5,
            "last4": "9876",
        },
    )
    assert card.status_code == 201
    bad_card = api(
        client,
        "POST",
        "/api/v1/cards",
        json={
            "name": "X",
            "limit_cents": 1,
            "closing_day": 1,
            "due_day": 1,
            "last4": "4111111111111111",
        },
    )
    assert bad_card.status_code == 422
    purchase = api(
        client,
        "POST",
        f"/api/v1/cards/{card.json()['id']}/purchases",
        json={
            "category_id": str(category(db, user, "Compras").id),
            "description": "Fone",
            "purchase_date": "2026-09-10",
            "total_cents": 10_000,
            "installments": 3,
        },
        headers={"Idempotency-Key": "purchase-api-0001"},
    )
    assert purchase.status_code == 201
    assert [i["amount_cents"] for i in purchase.json()["installments"]] == [3_334, 3_333, 3_333]
    statements = client.get(f"/api/v1/cards/{card.json()['id']}/statements").json()
    first = statements[0]
    pay = api(
        client,
        "POST",
        f"/api/v1/statements/{first['statement_id']}/payments",
        json={
            "account_id": str(account.id),
            "amount_cents": first["remaining_cents"],
            "payment_date": "2026-10-01",
        },
        headers={"Idempotency-Key": "payment-api-0001"},
    )
    assert pay.status_code == 201
    assert client.get(f"/api/v1/statements/{first['statement_id']}").json()["status"] == "paid"

    csv_content = "Data;Descrição;Valor\n01/10/2026;Venda;100,00\n"
    upload = client.post(
        "/api/v1/imports",
        data={"account_id": str(account.id)},
        files={"file": ("x.csv", csv_content.encode(), "text/csv")},
        headers={
            "X-CSRF-Token": __import__("tests.conftest", fromlist=["csrf_for"]).csrf_for(client)
        },
    )
    assert upload.status_code == 201
    batch_id = upload.json()["id"]
    assert (
        api(
            client,
            "POST",
            f"/api/v1/imports/{batch_id}/mapping",
            json={"date_col": 0, "description_col": 1, "amount_col": 2},
        ).json()["valid"]
        == 1
    )
    rows = [r["id"] for r in client.get(f"/api/v1/imports/{batch_id}/rows").json() if r["selected"]]
    confirm = api(
        client,
        "POST",
        f"/api/v1/imports/{batch_id}/confirm",
        json={"row_ids": rows},
        headers={"Idempotency-Key": "confirm-api-0001"},
    )
    assert confirm.json()["importadas"] == 1

    report = client.get(
        "/api/v1/reports/receitas-despesas", params={"start": "2026-09", "end": "2026-10"}
    )
    assert report.status_code == 200 and report.json()["regime"] == "Competência"
    pdf = client.get(
        "/api/v1/reports/cartoes", params={"start": "2026-09", "end": "2026-12", "format": "pdf"}
    )
    assert pdf.content.startswith(b"%PDF")
