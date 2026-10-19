"""Concorrência: confirmação duplicada de importação e gerador de recorrências simultâneo."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock
from app.core.config import get_settings
from app.domain.ledger import TransactionType
from app.domain.recurrence import Frequency
from app.jobs import recurrences as recurrence_job
from app.models import Account, RecurrenceRule, Transaction
from app.services.imports import ImportService, Mapping
from app.services.recurrences import RecurrenceInput, RecurrenceService
from tests.concurrency.conftest import run_concurrently
from tests.conftest import make_user
from tests.factories import audit, category, make_account

pytestmark = pytest.mark.concurrency

CSV = "Data;Descrição;Valor\n01/10/2026;Receita A;100,00\n02/10/2026;Despesa B;-30,00\n03/10/2026;Despesa C;-20,00\n"


def test_duplicate_import_confirmation_applies_once(real_sessions: sessionmaker[Session]) -> None:
    with real_sessions() as db:
        user = make_user(db, email=f"imp-{uuid.uuid4().hex[:8]}@example.com")
        account = make_account(db, user, initial=0)
        svc = ImportService(db, Clock(), audit(db), get_settings())
        batch = svc.upload(user.id, str(account.id), "x.csv", CSV.encode())
        svc.apply_mapping(user.id, str(batch.id), Mapping(0, 1, 2, "dd/mm/aaaa", True))
        rows = [str(r.id) for r in svc.rows(batch) if r.selected]
        user_id, batch_id, account_id = user.id, str(batch.id), account.id

    def confirm(key: str) -> object:
        with real_sessions() as db:
            return ImportService(db, Clock(), audit(db), get_settings()).confirm(user_id, batch_id, rows, idempotency_key=key)

    results = run_concurrently([lambda: confirm("dup-confirm-0001"), lambda: confirm("dup-confirm-0002"), lambda: confirm("dup-confirm-0001")])
    assert not [r for r in results if isinstance(r, Exception)], results
    with real_sessions() as db:
        assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.user_id == user_id)) == 3
        account = db.get(Account, account_id)
        assert account is not None and account.current_balance_cents == 5_000


def test_concurrent_recurrence_jobs_never_duplicate(real_sessions: sessionmaker[Session]) -> None:
    with real_sessions() as db:
        user = make_user(db, email=f"rec-{uuid.uuid4().hex[:8]}@example.com")
        account = make_account(db, user, initial=1_000_000)
        service = RecurrenceService(db, Clock(), audit(db), horizon_days=90)
        rule = service.create(
            user.id,
            RecurrenceInput(
                type=TransactionType.EXPENSE, account_id=account.id, category_id=category(db, user, "Moradia").id,
                description="Condomínio", amount_cents=50_000, frequency=Frequency.WEEKLY, start_date=date(2026, 10, 5),
            ),
        )
        rule.generated_until = date(2026, 10, 4)  # simula falha anterior do agendador: nada gerado
        db.execute(Transaction.__table__.delete().where(Transaction.recurrence_id == rule.id))
        db.commit()
        rule_id = rule.id

    results = run_concurrently([lambda: recurrence_job.run_once(real_sessions)] * 4)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert sum(1 for r in results if r.acquired) >= 1
    with real_sessions() as db:
        dates = db.scalars(select(Transaction.occurrence_date).where(Transaction.recurrence_id == rule_id)).all()
        assert len(dates) == len(set(dates)) > 0  # RN045/RN050: recuperado e sem duplicatas
        rule = db.get(RecurrenceRule, rule_id)
        assert rule is not None and rule.generated_until is not None

    # Reexecução após conclusão não cria nada novo (idempotência do job — RN048).
    before = len(dates)
    recurrence_job.run_once(real_sessions)
    with real_sessions() as db:
        assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.recurrence_id == rule_id)) == before
