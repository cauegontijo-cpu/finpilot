"""Cartões de crédito e parcelamentos (RN061–RN070)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import FrozenClock
from app.core.errors import BusinessRuleViolation, InsufficientFunds, ValidationFailed
from app.domain.dates import Month
from app.models import (
    CardInstallment,
    CardStatement,
    InstallmentKind,
    InstallmentStatus,
    Transaction,
)
from app.services.analytics import AnalyticsService
from app.services.budgets import BudgetService
from app.services.card_queries import card_debt, statement_balances
from app.services.cards import CardInput, CardService, PurchaseInput
from app.services.forecast import ForecastService
from tests.conftest import post_form
from tests.factories import audit, category, make_account

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


def svc(db: Session, now: datetime = NOW) -> CardService:
    return CardService(db, FrozenClock(now), audit(db))


def make_card(db: Session, user: Any, limit: int = 500_000, closing: int = 25, due: int = 5) -> Any:
    return svc(db).create(
        user.id,
        CardInput(name="Visa", limit_cents=limit, closing_day=closing, due_day=due, last4="4321"),
    )


def buy(
    db: Session,
    user: Any,
    card: Any,
    total: int,
    n: int = 1,
    when: date = date(2026, 10, 10),
    now: datetime = NOW,
    cat: str = "Compras",
) -> Any:
    return svc(db, now).purchase(
        user.id,
        str(card.id),
        PurchaseInput(
            category_id=category(db, user, cat).id,
            description="Compra",
            purchase_date=when,
            total_cents=total,
            installments=n,
        ),
        idempotency_key=uuid.uuid4().hex,
    )


def installments(db: Session, purchase: Any) -> list[CardInstallment]:
    return list(
        db.scalars(
            select(CardInstallment)
            .where(CardInstallment.purchase_id == purchase.id)
            .order_by(CardInstallment.kind, CardInstallment.number)
        )
    )


def statement_month(db: Session, inst: CardInstallment) -> tuple[int, int]:
    s = db.get(CardStatement, inst.statement_id)
    assert s is not None
    return s.year, s.month


def test_installments_sum_exactly_and_follow_statements(db: Session, user: Any) -> None:
    card = make_card(db, user)
    purchase = buy(db, user, card, 100_000, 3, when=date(2026, 10, 10))
    items = installments(db, purchase)
    assert [i.amount_cents for i in items] == [33_334, 33_333, 33_333]  # RN061/RN062
    assert sum(i.amount_cents for i in items) == purchase.total_cents
    assert [statement_month(db, i) for i in items] == [(2026, 10), (2026, 11), (2026, 12)]
    first = db.get(CardStatement, items[0].statement_id)
    assert (first.closing_date, first.due_date) == (date(2026, 10, 25), date(2026, 11, 5))  # RN067


def test_purchase_after_closing_and_year_rollover(db: Session, user: Any) -> None:
    card = make_card(db, user)
    purchase = buy(
        db,
        user,
        card,
        30_000,
        3,
        when=date(2026, 10, 26),
        now=datetime(2026, 10, 27, 12, tzinfo=UTC),
    )  # após fechamento (25)
    assert [statement_month(db, i) for i in installments(db, purchase)] == [
        (2026, 11),
        (2026, 12),
        (2027, 1),
    ]
    late = svc(db, datetime(2026, 12, 28, 12, tzinfo=UTC)).purchase(
        user.id,
        str(card.id),
        PurchaseInput(
            category_id=category(db, user, "Lazer").id,
            description="Viagem",
            purchase_date=date(2026, 12, 28),
            total_cents=10_000,
        ),
        idempotency_key=uuid.uuid4().hex,
    )
    assert [statement_month(db, i) for i in installments(db, late)] == [(2027, 1)]


def test_february_closing_day_beyond_month_end(db: Session, user: Any) -> None:
    card = make_card(db, user, closing=30, due=10)
    feb = datetime(2027, 2, 28, 12, tzinfo=UTC)
    on_close = svc(db, feb).purchase(
        user.id,
        str(card.id),
        PurchaseInput(
            category_id=category(db, user, "Compras").id,
            description="X",
            purchase_date=date(2027, 2, 28),
            total_cents=1_000,
        ),
        idempotency_key=uuid.uuid4().hex,
    )
    s = db.get(CardStatement, installments(db, on_close)[0].statement_id)
    assert (s.year, s.month, s.closing_date, s.due_date) == (
        2027,
        2,
        date(2027, 2, 28),
        date(2027, 3, 10),
    )


def test_limit_is_committed_and_purchase_over_limit_refused(db: Session, user: Any) -> None:
    card = make_card(db, user, limit=100_000)
    buy(db, user, card, 60_000, 6)
    assert card_debt(db, user.id, card.id) == 60_000  # RN064: todo o valor não liquidado
    with pytest.raises(BusinessRuleViolation, match="Limite"):
        buy(db, user, card, 40_001)
    buy(db, user, card, 40_000)
    assert card_debt(db, user.id, card.id) == 100_000


def test_inconsistent_installments_rejected(db: Session, user: Any) -> None:
    card = make_card(db, user)
    with pytest.raises(ValidationFailed):
        buy(db, user, card, 5, 6)  # RN063: parcela menor que R$ 0,01
    with pytest.raises(ValidationFailed):
        buy(db, user, card, 5_000, 49)


def test_payment_reduces_cash_not_expense_and_frees_limit(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=200_000)
    card = make_card(db, user, limit=300_000)
    buy(db, user, card, 90_000, 1, when=date(2026, 9, 20), cat="Compras")  # fatura de set/2026
    statement = statement_balances(db, user.id, card_id=card.id)[0]
    analytics = AnalyticsService(db)
    expense_before = analytics.monthly_competence(user.id, Month(2026, 9), Month(2026, 10))
    payment = svc(db).pay(
        user.id,
        str(statement.statement_id),
        account.id,
        50_000,
        date(2026, 10, 5),
        idempotency_key=uuid.uuid4().hex,
    )
    db.refresh(account)
    assert account.current_balance_cents == 150_000  # RN065
    expense_after = analytics.monthly_competence(user.id, Month(2026, 9), Month(2026, 10))
    assert (
        [m.expense for m in expense_after] == [m.expense for m in expense_before] == [90_000, 0]
    )  # RN066
    flow = analytics.cash_flow(user.id, date(2026, 10, 1), date(2026, 10, 31))
    assert (flow.outflow, flow.card_payments) == (50_000, 50_000)
    after = statement_balances(db, user.id, statement_ids=[statement.statement_id])[0]
    assert after.remaining_cents == 40_000 and after.status(date(2026, 10, 15)) == "overdue"
    assert card_debt(db, user.id, card.id) == 40_000  # RN068
    tx = db.get(Transaction, payment.transaction_id)
    assert tx.type.value == "card_payment" and tx.category_id is None


def test_overpayment_refused(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=200_000)
    card = make_card(db, user)
    buy(db, user, card, 10_000, when=date(2026, 9, 20))
    sid = str(statement_balances(db, user.id, card_id=card.id)[0].statement_id)
    with pytest.raises(BusinessRuleViolation, match="excede"):
        svc(db).pay(
            user.id, sid, account.id, 10_001, date(2026, 10, 5), idempotency_key=uuid.uuid4().hex
        )  # RN069
    svc(db).pay(
        user.id, sid, account.id, 10_000, date(2026, 10, 5), idempotency_key=uuid.uuid4().hex
    )
    assert statement_balances(db, user.id, card_id=card.id)[0].status(date(2026, 10, 15)) == "paid"


def test_payment_without_funds_refused(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000)
    card = make_card(db, user)
    buy(db, user, card, 10_000, when=date(2026, 9, 20))
    sid = str(statement_balances(db, user.id, card_id=card.id)[0].statement_id)
    with pytest.raises(InsufficientFunds):
        svc(db).pay(
            user.id, sid, account.id, 5_000, date(2026, 10, 5), idempotency_key=uuid.uuid4().hex
        )


def test_cancel_payment_restores_account_and_debt(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=100_000)
    card = make_card(db, user)
    buy(db, user, card, 30_000, when=date(2026, 9, 20))
    sid = str(statement_balances(db, user.id, card_id=card.id)[0].statement_id)
    payment = svc(db).pay(
        user.id, sid, account.id, 30_000, date(2026, 10, 5), idempotency_key=uuid.uuid4().hex
    )
    svc(db).cancel_payment(user.id, str(payment.id))
    db.refresh(account)
    assert account.current_balance_cents == 100_000
    assert card_debt(db, user.id, card.id) == 30_000


def test_refund_before_closing_cancels_open_installments(db: Session, user: Any) -> None:
    card = make_card(db, user)
    purchase = buy(db, user, card, 60_000, 3, when=date(2026, 10, 10))
    svc(db).refund(user.id, str(purchase.id))
    assert {i.status for i in installments(db, purchase)} == {InstallmentStatus.CANCELLED}
    assert card_debt(db, user.id, card.id) == 0
    assert (
        AnalyticsService(db)
        .monthly_competence(user.id, Month(2026, 10), Month(2026, 12))[0]
        .expense
        == 0
    )


def test_refund_after_closing_creates_credit_and_preserves_history(db: Session, user: Any) -> None:
    card = make_card(db, user)
    purchase = buy(db, user, card, 90_000, 3, when=date(2026, 9, 10))  # set (fechada), out, nov
    svc(db).refund(
        user.id, str(purchase.id)
    )  # hoje: 15/10, fatura de out ainda aberta (fecha 25/10)
    items = installments(db, purchase)
    charges = [i for i in items if i.kind is InstallmentKind.CHARGE]
    credits = [i for i in items if i.kind is InstallmentKind.CREDIT]
    assert [i.status for i in charges] == [
        InstallmentStatus.ACTIVE,
        InstallmentStatus.CANCELLED,
        InstallmentStatus.CANCELLED,
    ]
    assert len(credits) == 1 and credits[0].amount_cents == 30_000
    assert statement_month(db, credits[0]) == (2026, 10)
    assert card_debt(db, user.id, card.id) == 0  # RN068/RN070
    months = AnalyticsService(db).monthly_competence(user.id, Month(2026, 9), Month(2026, 11))
    assert [m.expense for m in months] == [
        30_000,
        -30_000,
        0,
    ]  # estorno reconhecido na competência do crédito


def test_installments_count_in_budget_competence(db: Session, user: Any) -> None:
    card = make_card(db, user)
    budgets = BudgetService(db, FrozenClock(NOW), audit(db))
    budgets.create(user.id, category(db, user, "Lazer").id, Month(2026, 11), 50_000)
    buy(db, user, card, 120_000, 3, when=date(2026, 10, 10), cat="Lazer")  # parcelas: out, nov, dez
    item = budgets.overview(user.id, Month(2026, 11)).items[0]
    assert item.used == 40_000  # RN031


def test_forecast_includes_statement_due_as_cash_outflow(db: Session, user: Any) -> None:
    make_account(db, user, initial=100_000)
    card = make_card(db, user)
    buy(db, user, card, 30_000, 2, when=date(2026, 10, 10))
    f = ForecastService(db).build(user.id, date(2026, 10, 15), 60)
    card_events = [e for e in f.events if e.source == "card"]
    assert [(e.day, e.amount_cents) for e in card_events] == [
        (date(2026, 11, 5), -15_000),
        (date(2026, 12, 5), -15_000),
    ]  # RN075


def test_card_pages_and_isolation(
    auth_client: TestClient, user: Any, other_user: Any, db: Session
) -> None:
    hx = {"HX-Request": "true"}
    response = post_form(
        auth_client,
        "/app/cartoes",
        {
            "name": "Master",
            "limit": "5.000,00",
            "closing_day": "25",
            "due_day": "5",
            "last4": "1234",
            "color": "#0b1b3f",
        },
        headers=hx,
    )
    assert response.status_code == 204
    bad = post_form(
        auth_client,
        "/app/cartoes",
        {
            "name": "X",
            "limit": "1,00",
            "closing_day": "25",
            "due_day": "5",
            "last4": "1234567890123456",
        },
        headers=hx,
    )
    assert bad.status_code == 422 and "4 últimos" in bad.text
    assert auth_client.get("/app/cartoes").status_code == 200
    theirs = make_card(db, other_user)
    buy(db, other_user, theirs, 10_000, when=date(2026, 9, 1))
    sid = statement_balances(db, other_user.id, card_id=theirs.id)[0].statement_id
    assert auth_client.get(f"/app/cartoes/{theirs.id}").status_code == 404
    assert auth_client.get(f"/app/faturas/{sid}").status_code == 404
    assert (
        post_form(
            auth_client,
            f"/app/faturas/{sid}/pagar",
            {"amount": "1,00", "payment_date": "2026-10-01"},
            headers=hx,
        ).status_code
        == 404
    )
