"""Regras do núcleo financeiro: contas, transações e transferências (RN001–RN028)."""

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import (
    BusinessRuleViolation,
    IdempotencyConflict,
    InsufficientFunds,
    NotFoundError,
    ValidationFailed,
)
from app.domain.ledger import TransactionStatus, TransactionType
from app.models import Account, AuditLog, CategoryKind, Transaction
from app.services.accounts import AccountInput
from app.services.transactions import TransactionFilters, TransactionInput
from app.services.transfers import TransferInput
from tests.factories import (
    accounts,
    category,
    key,
    make_account,
    make_transfer,
    make_tx,
    transactions,
    transfers,
)


def reconcile(db: Session, account: Account) -> int:
    """Saldo recalculado a partir do histórico (prova de consistência do saldo desnormalizado)."""
    rows = db.execute(
        select(Transaction.type, func.sum(Transaction.amount_cents))
        .where(Transaction.account_id == account.id, Transaction.status == "settled")
        .group_by(Transaction.type)
    ).all()
    total = account.initial_balance_cents
    for tx_type, amount in rows:
        total += amount if tx_type in ("income", "transfer_in") else -amount
    return total


# ---------------------------------------------------------------- contas
def test_initial_balance_is_not_income(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=50_000)
    assert account.current_balance_cents == 50_000
    totals = transactions(db).totals(user.id, TransactionFilters())
    assert totals.income_settled == 0  # RN003


def test_settled_income_and_expense_change_balance(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    make_tx(db, user, account, 5_000, tx_type=TransactionType.INCOME)
    make_tx(db, user, account, 3_000)
    db.refresh(account)
    assert account.current_balance_cents == 12_000  # RN005/RN006
    assert reconcile(db, account) == account.current_balance_cents


def test_planned_does_not_change_balance(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    make_tx(db, user, account, 9_000, status=TransactionStatus.PLANNED, when=date(2026, 12, 1))
    db.refresh(account)
    assert account.current_balance_cents == 10_000  # RN013


def test_negative_balance_refused_when_not_allowed(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000)
    with pytest.raises(InsufficientFunds):
        make_tx(db, user, account, 1_001)  # RN008
    db.rollback()  # como no ciclo de requisição: exceção → rollback
    db.refresh(account)
    assert account.current_balance_cents == 1_000
    assert db.scalar(select(func.count()).select_from(Transaction)) == 0


def test_negative_balance_allowed_when_configured(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000, allow_negative=True)
    make_tx(db, user, account, 5_000)
    db.refresh(account)
    assert account.current_balance_cents == -4_000


def test_account_with_history_cannot_be_deleted_only_archived(db: Session, user: Any) -> None:
    account = make_account(db, user)
    make_tx(db, user, account, 100)
    with pytest.raises(BusinessRuleViolation, match="histórico"):
        accounts(db).delete(user.id, str(account.id))  # RN009
    archived = accounts(db).set_archived(user.id, str(account.id), True)
    assert archived.archived_at is not None
    with pytest.raises(BusinessRuleViolation, match="arquivada"):
        make_tx(db, user, account, 100)


def test_account_without_history_can_be_deleted(db: Session, user: Any) -> None:
    account = make_account(db, user)
    accounts(db).delete(user.id, str(account.id))
    assert db.get(Account, account.id) is None


def test_change_initial_balance_adjusts_current_by_delta(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    make_tx(db, user, account, 2_500)
    updated = accounts(db).update(
        user.id,
        str(account.id),
        AccountInput(
            name=account.name,
            type=account.type,
            initial_balance_cents=20_000,
            start_date=account.start_date,
        ),
    )
    assert updated.current_balance_cents == 17_500
    assert reconcile(db, updated) == 17_500
    log = db.scalars(select(AuditLog).where(AuditLog.event_type == "account.updated")).one()
    assert log.changes["initial_balance_cents"] == {"antes": 10_000, "depois": 20_000}


def test_duplicate_account_name_conflict(db: Session, user: Any) -> None:
    make_account(db, user, name="Nubank")
    with pytest.raises(Exception, match="Já existe"):
        make_account(db, user, name="nubank")


# ---------------------------------------------------------------- isolamento
def test_user_cannot_use_other_users_account_or_category(
    db: Session, user: Any, other_user: Any
) -> None:
    mine = make_account(db, user)
    theirs = make_account(db, other_user, name="Conta do Bruno")
    their_category = category(db, other_user, "Alimentação")
    service = transactions(db)
    base = TransactionInput(
        type=TransactionType.EXPENSE,
        account_id=theirs.id,
        category_id=category(db, user, "Alimentação").id,
        description="x",
        amount_cents=100,
        competence_date=date(2026, 10, 1),
    )
    with pytest.raises(NotFoundError):
        service.create(user.id, base, idempotency_key=key())  # RN002
    with pytest.raises(NotFoundError):
        service.create(
            user.id,
            TransactionInput(
                **{**base.__dict__, "account_id": mine.id, "category_id": their_category.id}
            ),
            idempotency_key=key(),
        )  # RN012


def test_category_kind_must_match_type(db: Session, user: Any) -> None:
    account = make_account(db, user)
    with pytest.raises(BusinessRuleViolation, match="não é de receita"):
        transactions(db).create(
            user.id,
            TransactionInput(
                type=TransactionType.INCOME,
                account_id=account.id,
                category_id=category(db, user, "Alimentação").id,
                description="x",
                amount_cents=100,
                competence_date=date(2026, 10, 1),
            ),
            idempotency_key=key(),
        )


@pytest.mark.parametrize("amount", [0, -100])
def test_amount_must_be_positive(db: Session, user: Any, amount: int) -> None:
    account = make_account(db, user)
    with pytest.raises(ValidationFailed):
        make_tx(db, user, account, amount)  # RN011


def test_settlement_cannot_be_in_future(db: Session, user: Any) -> None:
    account = make_account(db, user)
    with pytest.raises(ValidationFailed):
        make_tx(db, user, account, 100, when=Clock().today() + timedelta(days=3))


# ---------------------------------------------------------------- idempotência
def test_repeated_request_with_same_key_applies_once(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    first = make_tx(db, user, account, 1_000, idempotency_key="chave-repetida-0001")
    second = make_tx(db, user, account, 1_000, idempotency_key="chave-repetida-0001")
    assert first.id == second.id  # RN017
    db.refresh(account)
    assert account.current_balance_cents == 9_000


def test_same_key_with_different_payload_conflicts(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    make_tx(db, user, account, 1_000, idempotency_key="chave-conflito-0001")
    with pytest.raises(IdempotencyConflict):
        make_tx(db, user, account, 2_000, idempotency_key="chave-conflito-0001")


# ---------------------------------------------------------------- edição / cancelamento
def test_cancel_settled_reverses_and_keeps_history(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    tx = make_tx(db, user, account, 4_000)
    transactions(db).cancel(user.id, str(tx.id))
    db.refresh(account)
    assert account.current_balance_cents == 10_000  # RN015
    assert db.get(Transaction, tx.id).status is TransactionStatus.CANCELLED
    events = [log.event_type for log in transactions(db).history(user.id, str(tx.id))]
    assert events == ["transaction.created", "transaction.reversed"]
    with pytest.raises(BusinessRuleViolation):
        transactions(db).cancel(user.id, str(tx.id))


def test_cancel_income_that_would_make_balance_negative_is_refused(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=0)
    income = make_tx(db, user, account, 5_000, tx_type=TransactionType.INCOME)
    make_tx(db, user, account, 4_000)
    with pytest.raises(InsufficientFunds):
        transactions(db).cancel(user.id, str(income.id))


def test_edit_settled_moves_amount_between_accounts_consistently(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=10_000)
    b = make_account(db, user, name="B", initial=10_000)
    tx = make_tx(db, user, a, 3_000)
    data = TransactionInput(
        type=TransactionType.EXPENSE,
        account_id=b.id,
        category_id=tx.category_id,
        description="Mercado (corrigido)",
        amount_cents=4_500,
        competence_date=tx.competence_date,
        settlement_date=tx.settlement_date,
    )
    transactions(db).update(user.id, str(tx.id), data)  # RN016
    db.refresh(a)
    db.refresh(b)
    assert (a.current_balance_cents, b.current_balance_cents) == (10_000, 5_500)
    assert (
        reconcile(db, a) == a.current_balance_cents and reconcile(db, b) == b.current_balance_cents
    )
    log = db.scalars(select(AuditLog).where(AuditLog.event_type == "transaction.updated")).one()
    assert log.changes["amount_cents"] == {"antes": 3_000, "depois": 4_500}


def test_edit_increasing_expense_beyond_balance_refused(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=5_000)
    tx = make_tx(db, user, account, 4_000)
    data = TransactionInput(
        type=TransactionType.EXPENSE,
        account_id=account.id,
        category_id=tx.category_id,
        description=tx.description,
        amount_cents=5_001,
        competence_date=tx.competence_date,
        settlement_date=tx.settlement_date,
    )
    with pytest.raises(InsufficientFunds):
        transactions(db).update(user.id, str(tx.id), data)


def test_settle_planned(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=10_000)
    tx = make_tx(db, user, account, 2_000, status=TransactionStatus.PLANNED, when=date(2026, 10, 1))
    transactions(db).settle(user.id, str(tx.id), date(2026, 10, 2))
    transactions(db).settle(user.id, str(tx.id), date(2026, 10, 2))  # idempotente
    db.refresh(account)
    assert account.current_balance_cents == 8_000


# ---------------------------------------------------------------- transferências
def test_transfer_moves_money_without_income_or_expense(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=10_000)
    b = make_account(db, user, name="B", initial=0)
    make_transfer(db, user, a, b, 2_500)
    db.refresh(a)
    db.refresh(b)
    assert (a.current_balance_cents, b.current_balance_cents) == (7_500, 2_500)  # RN007
    totals = transactions(db).totals(user.id, TransactionFilters())
    assert totals.income_settled == totals.expense_settled == 0  # RN026


def test_transfer_same_account_refused(db: Session, user: Any) -> None:
    a = make_account(db, user)
    with pytest.raises(ValidationFailed):
        make_transfer(db, user, a, a, 100)  # RN021


def test_transfer_to_other_users_account_refused(db: Session, user: Any, other_user: Any) -> None:
    a = make_account(db, user)
    theirs = make_account(db, other_user, name="Bruno")
    with pytest.raises(NotFoundError):
        make_transfer(db, user, a, theirs, 100)  # RN022


def test_transfer_insufficient_funds_rolls_back_both_legs(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=1_000)
    b = make_account(db, user, name="B", initial=0)
    with pytest.raises(InsufficientFunds):
        make_transfer(db, user, a, b, 1_001)  # RN025
    db.rollback()
    db.refresh(a)
    db.refresh(b)
    assert (a.current_balance_cents, b.current_balance_cents) == (1_000, 0)
    assert (
        db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.transfer_id.is_not(None))
        )
        == 0
    )


def test_transfer_cancel_reverts_both_sides(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=10_000)
    b = make_account(db, user, name="B", initial=0)
    transfer = make_transfer(db, user, a, b, 4_000)
    transfers(db).cancel(user.id, str(transfer.id))  # RN027
    db.refresh(a)
    db.refresh(b)
    assert (a.current_balance_cents, b.current_balance_cents) == (10_000, 0)
    legs = transfers(db).legs(transfer)
    assert {leg.status for leg in legs} == {TransactionStatus.CANCELLED}


def test_transfer_cancel_refused_if_destination_already_spent(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=10_000)
    b = make_account(db, user, name="B", initial=0)
    transfer = make_transfer(db, user, a, b, 4_000)
    make_tx(db, user, b, 3_000)
    with pytest.raises(InsufficientFunds):
        transfers(db).cancel(user.id, str(transfer.id))


def test_transfer_idempotency(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=10_000)
    b = make_account(db, user, name="B", initial=0)
    data = TransferInput(
        from_account_id=a.id,
        to_account_id=b.id,
        amount_cents=1_000,
        transfer_date=date(2026, 10, 1),
    )
    first = transfers(db).create(user.id, data, idempotency_key="transf-key-0001")
    second = transfers(db).create(user.id, data, idempotency_key="transf-key-0001")
    assert first.id == second.id  # RN028
    db.refresh(a)
    assert a.current_balance_cents == 9_000


def test_planned_transfer_then_settle(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=10_000)
    b = make_account(db, user, name="B", initial=0)
    transfer = make_transfer(
        db, user, a, b, 1_000, when=date(2026, 12, 20), status=TransactionStatus.PLANNED
    )
    db.refresh(a)
    assert a.current_balance_cents == 10_000
    transfers(db).settle(user.id, str(transfer.id))
    db.refresh(a)
    db.refresh(b)
    assert (a.current_balance_cents, b.current_balance_cents) == (9_000, 1_000)


def test_transaction_listing_filters_and_search(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=100_000)
    make_tx(db, user, account, 1_000, description="Padaria 50% off", when=date(2026, 9, 5))
    make_tx(
        db,
        user,
        account,
        2_000,
        description="Supermercado",
        when=date(2026, 10, 5),
        category_name="Compras",
    )
    make_tx(
        db,
        user,
        account,
        9_000,
        tx_type=TransactionType.INCOME,
        description="Salário",
        when=date(2026, 10, 5),
    )
    service = transactions(db)
    october = service.list(
        user.id, TransactionFilters(start=date(2026, 10, 1), end=date(2026, 10, 31))
    )
    assert october.total == 2
    assert service.list(user.id, TransactionFilters(q="50%")).total == 1  # curinga escapado
    assert service.list(user.id, TransactionFilters(q="%")).total == 1
    assert service.list(user.id, TransactionFilters(kind="income")).total == 1
    compras = category(db, user, "Compras", CategoryKind.EXPENSE)
    assert service.list(user.id, TransactionFilters(category_id=compras.id)).total == 1
    ordered = service.list(user.id, TransactionFilters(sort="amount"))
    assert [t.amount_cents for t in ordered.items] == [1_000, 2_000, 9_000]
    with pytest.raises(ValidationFailed):
        service.list(user.id, TransactionFilters(sort="password_hash"))
