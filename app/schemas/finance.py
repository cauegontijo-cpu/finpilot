"""Schemas de contas, categorias, transações, transferências e orçamentos."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, computed_field

from app.domain.ledger import TransactionOrigin, TransactionStatus, TransactionType
from app.domain.money import cents_to_decimal
from app.models import AccountType, CategoryKind
from app.schemas.common import InputModel, MoneyMixin, OutputModel, PositiveCents, SignedCents


# ---------------------------------------------------------------- contas
class AccountIn(InputModel):
    name: str = Field(min_length=1, max_length=60)
    type: AccountType
    initial_balance_cents: SignedCents = 0
    start_date: date
    allow_negative: bool = False
    institution: str | None = Field(default=None, max_length=80)
    color: str = "#2563eb"
    icon: str = "wallet"


class AccountOut(OutputModel):
    id: uuid.UUID
    name: str
    type: AccountType
    institution: str | None
    color: str
    icon: str
    initial_balance_cents: int
    current_balance_cents: int
    start_date: date
    allow_negative: bool
    archived_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def current_balance(self) -> Decimal:
        return cents_to_decimal(self.current_balance_cents)


# ---------------------------------------------------------------- categorias
class CategoryIn(InputModel):
    name: str = Field(min_length=1, max_length=50)
    kind: CategoryKind
    color: str = "#64748b"
    icon: str = "tag"


class CategoryOut(OutputModel):
    id: uuid.UUID
    name: str
    kind: CategoryKind
    color: str
    icon: str
    archived_at: datetime | None


# ---------------------------------------------------------------- transações
class TransactionIn(InputModel):
    type: TransactionType = Field(description="income ou expense")
    account_id: uuid.UUID
    category_id: uuid.UUID
    description: str = Field(min_length=1, max_length=140)
    amount_cents: PositiveCents
    competence_date: date
    status: TransactionStatus = TransactionStatus.SETTLED
    settlement_date: date | None = None
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class TransactionUpdate(InputModel):
    account_id: uuid.UUID
    category_id: uuid.UUID
    description: str = Field(min_length=1, max_length=140)
    amount_cents: PositiveCents
    competence_date: date
    status: TransactionStatus = TransactionStatus.SETTLED
    settlement_date: date | None = None
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class SettleIn(InputModel):
    settlement_date: date | None = None


class TransactionOut(MoneyMixin):
    id: uuid.UUID
    account_id: uuid.UUID
    type: TransactionType
    status: TransactionStatus
    origin: TransactionOrigin
    description: str
    category_id: uuid.UUID | None
    competence_date: date
    settlement_date: date | None
    due_date: date | None
    notes: str | None
    transfer_id: uuid.UUID | None
    recurrence_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class TotalsOut(OutputModel):
    income_settled: int
    expense_settled: int
    income_planned: int
    expense_planned: int
    net_settled: int


class HistoryEntryOut(OutputModel):
    event_type: str
    outcome: str
    occurred_at: datetime
    changes: dict[str, object] | None
    request_id: str | None


# ---------------------------------------------------------------- transferências
class TransferIn(InputModel):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_cents: PositiveCents
    transfer_date: date
    description: str | None = Field(default=None, max_length=140)
    status: TransactionStatus = TransactionStatus.SETTLED


class TransferOut(MoneyMixin):
    id: uuid.UUID
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    transfer_date: date
    description: str | None
    status: TransactionStatus
    created_at: datetime


# ---------------------------------------------------------------- orçamentos
class BudgetIn(InputModel):
    category_id: uuid.UUID
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    limit_cents: PositiveCents


class BudgetUpdate(InputModel):
    limit_cents: PositiveCents


class BudgetStatusOut(OutputModel):
    id: uuid.UUID
    category_id: uuid.UUID
    category_name: str
    year: int
    month: int
    limit_cents: int
    used_cents: int
    planned_cents: int
    remaining_cents: int
    previous_used_cents: int
    percent_used: Decimal
    level: str = Field(description="ok | warning (≥80%) | over (≥100%)")
