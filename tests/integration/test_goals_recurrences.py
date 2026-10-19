"""Metas (RN038–RN043) e recorrências (RN044–RN050)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import FrozenClock
from app.core.errors import BusinessRuleViolation, ValidationFailed
from app.domain.ledger import TransactionStatus, TransactionType
from app.domain.recurrence import Frequency
from app.models import GoalStatus, RecurrenceStatus, Transaction
from app.services.goals import GoalInput, GoalService
from app.services.recurrences import RecurrenceInput, RecurrenceService
from tests.conftest import post_form
from tests.factories import audit, category, make_account

TODAY = date(2026, 10, 15)


def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 10, 15, 15, 0, tzinfo=UTC))


def goals(db: Session) -> GoalService:
    return GoalService(db, clock(), audit(db))


def recurrences(db: Session, c: FrozenClock | None = None) -> RecurrenceService:
    return RecurrenceService(db, c or clock(), audit(db), horizon_days=90)


# ---------------------------------------------------------------- metas
def test_goal_target_must_be_positive(db: Session, user: Any) -> None:
    with pytest.raises(ValidationFailed):
        goals(db).create(user.id, GoalInput(name="Viagem", target_cents=0))  # RN038


def test_allocations_are_virtual_and_do_not_touch_balance(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=100_000)
    service = goals(db)
    goal = service.create(user.id, GoalInput(name="Reserva", target_cents=50_000))
    service.allocate(user.id, str(goal.id), 20_000, TODAY)
    db.refresh(account)
    assert account.current_balance_cents == 100_000  # RN039/RN040
    assert db.scalar(select(func.count()).select_from(Transaction)) == 0
    summary = service.reservation_summary(user.id)
    assert (summary.available_cents, summary.reserved_cents, summary.free_cents) == (100_000, 20_000, 80_000)  # RN041


def test_over_reservation_is_flagged_without_artificial_entries(db: Session, user: Any) -> None:
    make_account(db, user, initial=10_000)
    service = goals(db)
    goal = service.create(user.id, GoalInput(name="Carro", target_cents=1_000_000))
    service.allocate(user.id, str(goal.id), 15_000, TODAY)
    assert service.reservation_summary(user.id).over_reserved  # RN042
    assert db.scalar(select(func.count()).select_from(Transaction)) == 0


def test_progress_status_and_reversal(db: Session, user: Any) -> None:
    service = goals(db)
    goal = service.create(user.id, GoalInput(name="Notebook", target_cents=10_000, deadline=date(2027, 4, 15)))
    first = service.allocate(user.id, str(goal.id), 6_000, date(2026, 9, 1))
    service.allocate(user.id, str(goal.id), 4_000, TODAY)
    db.refresh(goal)
    assert goal.status is GoalStatus.COMPLETED  # RN043
    service.reverse_allocation(user.id, str(goal.id), str(first.id))
    progress = service.progress(user.id, str(goal.id))
    assert progress.allocated == 4_000 and goal.status is GoalStatus.ACTIVE
    assert progress.required_monthly == 1_000  # 6.000 restantes em 6 meses
    with pytest.raises(BusinessRuleViolation):
        service.reverse_allocation(user.id, str(goal.id), str(first.id))
    assert len(service.allocations(user.id, str(goal.id))) == 3  # histórico preservado


def test_estimated_completion_uses_recent_average(db: Session, user: Any) -> None:
    service = goals(db)
    goal = service.create(user.id, GoalInput(name="Viagem", target_cents=90_000))
    for day in (date(2026, 8, 10), date(2026, 9, 10), date(2026, 10, 10)):
        service.allocate(user.id, str(goal.id), 10_000, day)
    p = service.progress(user.id, str(goal.id))
    assert p.monthly_average == 10_000
    assert p.estimated_completion == date(2027, 4, 15)  # faltam 60.000 → 6 meses


# ---------------------------------------------------------------- recorrências
def _rule(db: Session, user: Any, account: Any, **kw: Any) -> Any:
    data = RecurrenceInput(
        type=kw.pop("type", TransactionType.EXPENSE),
        account_id=account.id,
        category_id=category(db, user, kw.pop("category", "Moradia")).id,
        description=kw.pop("description", "Aluguel"),
        amount_cents=kw.pop("amount", 150_000),
        frequency=kw.pop("frequency", Frequency.MONTHLY),
        start_date=kw.pop("start", date(2026, 10, 31)),
        **kw,
    )
    return recurrences(db).create(user.id, data)


def test_generation_links_occurrences_and_uses_last_valid_day(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000_000)
    rule = _rule(db, user, account)
    occ = db.scalars(select(Transaction).where(Transaction.recurrence_id == rule.id).order_by(Transaction.occurrence_date)).all()
    assert [t.occurrence_date for t in occ] == [date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31)]  # RN046
    assert all(t.status is TransactionStatus.PLANNED for t in occ)  # RN049
    db.refresh(account)
    assert account.current_balance_cents == 1_000_000


def test_generation_is_idempotent(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000_000)
    rule = _rule(db, user, account)
    service = recurrences(db)
    rule.generated_until = None  # simula reprocessamento completo
    result = service.generate(rule, date(2027, 1, 31))
    assert result.created == 1  # só jan/2027 é nova; as demais já existiam (RN045/RN048)
    again = service.generate(rule, date(2027, 1, 31))
    assert again.created == 0


def test_edit_changes_only_future_planned_occurrences(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000_000)
    rule = _rule(db, user, account, start=date(2026, 9, 10))
    service = recurrences(db)
    past = db.scalars(
        select(Transaction).where(Transaction.recurrence_id == rule.id, Transaction.occurrence_date == date(2026, 9, 10))
    ).one()
    from app.services.transactions import TransactionService  # noqa: PLC0415

    TransactionService(db, clock(), audit(db)).settle(user.id, str(past.id), date(2026, 9, 10))
    data = RecurrenceInput(
        type=rule.type, account_id=account.id, category_id=rule.category_id, description="Aluguel reajustado",
        amount_cents=165_000, frequency=rule.frequency, start_date=rule.start_date,
    )
    service.update(user.id, str(rule.id), data)
    db.refresh(past)
    assert (past.amount_cents, past.description, past.status) == (150_000, "Aluguel", TransactionStatus.SETTLED)  # RN047
    future = db.scalars(
        select(Transaction).where(Transaction.recurrence_id == rule.id, Transaction.occurrence_date > TODAY)
    ).all()
    assert future and all(t.amount_cents == 165_000 for t in future)


def test_pause_resume_cancel(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000_000)
    rule = _rule(db, user, account)
    service = recurrences(db)
    service.pause(user.id, str(rule.id))
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.recurrence_id == rule.id)) == 0
    assert service.next_occurrences(rule) == []
    service.resume(user.id, str(rule.id))
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.recurrence_id == rule.id)) == 3
    service.cancel(user.id, str(rule.id))
    assert rule.status is RecurrenceStatus.CANCELLED
    with pytest.raises(BusinessRuleViolation):
        service.resume(user.id, str(rule.id))


def test_auto_settle_effective_when_enabled(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    rule = _rule(db, user, account, start=date(2026, 10, 1), amount=3_000, auto_settle=True, category="Assinaturas", description="Streaming")
    first = db.scalars(select(Transaction).where(Transaction.recurrence_id == rule.id, Transaction.occurrence_date == date(2026, 10, 1))).one()
    assert first.status is TransactionStatus.SETTLED
    db.refresh(account)
    assert account.current_balance_cents == 7_000


def test_auto_settle_without_funds_stays_planned(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=100)
    rule = _rule(db, user, account, start=date(2026, 10, 1), amount=3_000, auto_settle=True)
    first = db.scalars(select(Transaction).where(Transaction.recurrence_id == rule.id, Transaction.occurrence_date == date(2026, 10, 1))).one()
    assert first.status is TransactionStatus.PLANNED
    db.refresh(account)
    assert account.current_balance_cents == 100


# ---------------------------------------------------------------- telas
def test_goal_and_recurrence_pages(auth_client: TestClient, user: Any, db: Session) -> None:
    account = make_account(db, user, initial=100_000)
    hx = {"HX-Request": "true"}
    assert post_form(auth_client, "/app/metas", {"name": "Viagem", "target": "5.000,00", "color": "#7c3aed", "icon": "plane"}, headers=hx).status_code == 204
    assert auth_client.get("/app/metas").status_code == 200
    data = {
        "type": "income", "description": "Salário", "amount": "7.000,00", "account_id": str(account.id),
        "income_category_id": str(category(db, user, "Salário", kind=__import__("app.models", fromlist=["CategoryKind"]).CategoryKind.INCOME).id),
        "frequency": "monthly", "interval": "1", "start_date": "2026-11-05",
    }
    response = post_form(auth_client, "/app/recorrencias", data, headers=hx)
    assert response.status_code == 204, response.text
    assert "Salário" in auth_client.get("/app/recorrencias").text
