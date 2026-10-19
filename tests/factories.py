"""Factories de dados de teste construídas sobre os serviços reais (sem inserções diretas)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.domain.ledger import TransactionStatus, TransactionType
from app.models import Account, AccountType, Category, CategoryKind
from app.services.accounts import AccountInput, AccountService
from app.services.audit import AuditService, RequestMeta
from app.services.transactions import TransactionInput, TransactionService
from app.services.transfers import TransferInput, TransferService

META = RequestMeta(ip="127.0.0.1", request_id="test")


def audit(db: Session) -> AuditService:
    return AuditService(db, META)


def accounts(db: Session, clock: Clock | None = None) -> AccountService:
    return AccountService(db, clock or Clock(), audit(db))


def transactions(db: Session, clock: Clock | None = None) -> TransactionService:
    return TransactionService(db, clock or Clock(), audit(db))


def transfers(db: Session, clock: Clock | None = None) -> TransferService:
    return TransferService(db, clock or Clock(), audit(db))


def make_account(
    db: Session,
    user: Any,
    name: str = "Conta Corrente",
    initial: int = 100_000,
    allow_negative: bool = False,
    start: date = date(2025, 1, 1),
    clock: Clock | None = None,
) -> Account:
    return accounts(db, clock).create(
        user.id,
        AccountInput(
            name=name,
            type=AccountType.CHECKING,
            initial_balance_cents=initial,
            start_date=start,
            allow_negative=allow_negative,
        ),
    )


def category(db: Session, user: Any, name: str, kind: CategoryKind = CategoryKind.EXPENSE) -> Category:
    return db.scalars(
        select(Category).where(Category.user_id == user.id, Category.name == name, Category.kind == kind)
    ).one()


def key() -> str:
    return uuid.uuid4().hex


def make_tx(
    db: Session,
    user: Any,
    account: Account,
    amount: int,
    *,
    tx_type: TransactionType = TransactionType.EXPENSE,
    status: TransactionStatus = TransactionStatus.SETTLED,
    when: date = date(2026, 10, 10),
    category_name: str | None = None,
    description: str = "Lançamento de teste",
    due: date | None = None,
    clock: Clock | None = None,
    idempotency_key: str | None = None,
) -> Any:
    kind = CategoryKind.INCOME if tx_type is TransactionType.INCOME else CategoryKind.EXPENSE
    cat = category(db, user, category_name or ("Salário" if kind is CategoryKind.INCOME else "Alimentação"), kind)
    return transactions(db, clock).create(
        user.id,
        TransactionInput(
            type=tx_type,
            account_id=account.id,
            category_id=cat.id,
            description=description,
            amount_cents=amount,
            competence_date=when,
            status=status,
            settlement_date=when if status is TransactionStatus.SETTLED else None,
            due_date=due,
        ),
        idempotency_key=idempotency_key or key(),
    )


def make_transfer(
    db: Session, user: Any, source: Account, target: Account, amount: int, when: date = date(2026, 10, 10), **kw: Any
) -> Any:
    return transfers(db, kw.pop("clock", None)).create(
        user.id,
        TransferInput(from_account_id=source.id, to_account_id=target.id, amount_cents=amount, transfer_date=when, **kw),
        idempotency_key=key(),
    )
