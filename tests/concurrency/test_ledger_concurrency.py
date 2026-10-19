"""Condições de corrida em movimentações financeiras (RN010, RN017, RN028)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import InsufficientFunds
from app.domain.ledger import TransactionType
from app.models import Account, Transaction
from tests.concurrency.conftest import run_concurrently
from tests.conftest import make_user
from tests.factories import category, make_account, make_transfer, make_tx

pytestmark = pytest.mark.concurrency


@pytest.fixture
def setup(real_sessions: sessionmaker[Session]) -> dict[str, Any]:
    with real_sessions() as db:
        user = make_user(db, email=f"conc-{uuid.uuid4().hex[:8]}@example.com")
        a = make_account(db, user, name="A", initial=1_000)
        b = make_account(db, user, name="B", initial=1_000)
        food = category(db, user, "Alimentação")
        return {"user": user, "a": a.id, "b": b.id, "food": food.id}


def _balance(maker: sessionmaker[Session], account_id: uuid.UUID) -> int:
    with maker() as db:
        account = db.get(Account, account_id)
        assert account is not None
        return account.current_balance_cents


def test_two_simultaneous_expenses_cannot_overdraw(real_sessions: sessionmaker[Session], setup: dict[str, Any]) -> None:
    def spend() -> Any:
        with real_sessions() as db:
            account = db.get(Account, setup["a"])
            return make_tx(db, setup["user"], account, 700)  # type: ignore[arg-type]

    results = run_concurrently([spend, spend])
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], InsufficientFunds)
    assert _balance(real_sessions, setup["a"]) == 300


def test_many_concurrent_expenses_keep_balance_consistent(
    real_sessions: sessionmaker[Session], setup: dict[str, Any]
) -> None:
    def spend() -> Any:
        with real_sessions() as db:
            account = db.get(Account, setup["a"])
            return make_tx(db, setup["user"], account, 90)  # type: ignore[arg-type]

    results = run_concurrently([spend] * 15)
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 11  # 11 × 90 = 990 ≤ 1000 < 12 × 90
    assert all(isinstance(r, InsufficientFunds) for r in results if isinstance(r, Exception))
    assert _balance(real_sessions, setup["a"]) == 1_000 - 11 * 90


def test_opposite_concurrent_transfers_do_not_deadlock_or_lose_money(
    real_sessions: sessionmaker[Session], setup: dict[str, Any]
) -> None:
    def a_to_b() -> Any:
        with real_sessions() as db:
            return make_transfer(db, setup["user"], db.get(Account, setup["a"]), db.get(Account, setup["b"]), 100)

    def b_to_a() -> Any:
        with real_sessions() as db:
            return make_transfer(db, setup["user"], db.get(Account, setup["b"]), db.get(Account, setup["a"]), 50)

    results = run_concurrently([a_to_b, b_to_a] * 5)
    assert not [r for r in results if isinstance(r, Exception)]
    a, b = _balance(real_sessions, setup["a"]), _balance(real_sessions, setup["b"])
    assert a + b == 2_000  # dinheiro conservado
    assert a == 1_000 - 5 * 100 + 5 * 50


def test_duplicate_request_same_idempotency_key_applies_once(
    real_sessions: sessionmaker[Session], setup: dict[str, Any]
) -> None:
    def submit() -> Any:
        with real_sessions() as db:
            account = db.get(Account, setup["a"])
            tx = make_tx(
                db, setup["user"], account, 100, idempotency_key="duplo-clique-0001",  # type: ignore[arg-type]
                tx_type=TransactionType.EXPENSE, when=date(2026, 10, 1),
            )
            return tx.id

    results = run_concurrently([submit] * 4)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert len(set(results)) == 1
    with real_sessions() as db:
        count = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.user_id == setup["user"].id))
    assert count == 1
    assert _balance(real_sessions, setup["a"]) == 900
