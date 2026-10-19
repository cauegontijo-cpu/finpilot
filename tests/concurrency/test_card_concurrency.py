"""Concorrência em cartões: pagamento duplicado de fatura e compras simultâneas contra o limite."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation
from app.models import Account
from app.services.card_queries import card_debt, statement_balances
from app.services.cards import CardInput, CardService, PurchaseInput
from tests.concurrency.conftest import run_concurrently
from tests.conftest import make_user
from tests.factories import audit, category, make_account

pytestmark = pytest.mark.concurrency


def _setup(real_sessions: sessionmaker[Session], limit: int) -> dict[str, object]:
    with real_sessions() as db:
        user = make_user(db, email=f"card-{uuid.uuid4().hex[:8]}@example.com")
        account = make_account(db, user, initial=1_000_000)
        card = CardService(db, Clock(), audit(db)).create(
            user.id, CardInput(name="Visa", limit_cents=limit, closing_day=1, due_day=10)
        )
        return {
            "user": user,
            "account": account.id,
            "card": card.id,
            "category": category(db, user, "Compras").id,
        }


def test_duplicate_statement_payment_charges_once(real_sessions: sessionmaker[Session]) -> None:
    ctx = _setup(real_sessions, 500_000)
    user = ctx["user"]
    with real_sessions() as db:
        service = CardService(db, Clock(), audit(db))
        service.purchase(
            user.id,
            str(ctx["card"]),
            PurchaseInput(ctx["category"], "TV", date(2026, 9, 15), 80_000),
            idempotency_key=uuid.uuid4().hex,
        )  # type: ignore[attr-defined, arg-type]
        statement_id = str(statement_balances(db, user.id, card_id=ctx["card"])[0].statement_id)  # type: ignore[attr-defined, arg-type]

    def pay(key: str) -> object:
        with real_sessions() as db:
            return CardService(db, Clock(), audit(db)).pay(
                user.id,
                statement_id,
                ctx["account"],
                80_000,
                date(2026, 10, 9),
                idempotency_key=key,
            )  # type: ignore[attr-defined, arg-type]

    results = run_concurrently(
        [
            lambda: pay(uuid.uuid4().hex),
            lambda: pay(uuid.uuid4().hex),
            lambda: pay(uuid.uuid4().hex),
        ]
    )
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 2 and all(isinstance(e, BusinessRuleViolation) for e in errors)
    with real_sessions() as db:
        account = db.get(Account, ctx["account"])
        assert account is not None and account.current_balance_cents == 1_000_000 - 80_000
        assert (
            statement_balances(db, user.id, statement_ids=[uuid.UUID(statement_id)])[
                0
            ].remaining_cents
            == 0
        )  # type: ignore[attr-defined]


def test_concurrent_purchases_never_exceed_limit(real_sessions: sessionmaker[Session]) -> None:
    ctx = _setup(real_sessions, 100_000)
    user = ctx["user"]

    def buy() -> object:
        with real_sessions() as db:
            return CardService(db, Clock(), audit(db)).purchase(
                user.id,
                str(ctx["card"]),
                PurchaseInput(ctx["category"], "Item", date(2026, 10, 1), 30_000),  # type: ignore[attr-defined, arg-type]
                idempotency_key=uuid.uuid4().hex,
            )

    results = run_concurrently([buy] * 5)
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 3
    with real_sessions() as db:
        assert card_debt(db, user.id, ctx["card"]) == 90_000  # type: ignore[attr-defined, arg-type]
