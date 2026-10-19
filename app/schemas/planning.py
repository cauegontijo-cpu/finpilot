"""Schemas de metas, recorrências, cartões, faturas, importações e relatórios."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.domain.ledger import TransactionType
from app.domain.recurrence import Frequency
from app.models import GoalStatus, RecurrenceStatus
from app.schemas.common import InputModel, OutputModel, PositiveCents


class GoalIn(InputModel):
    name: str = Field(min_length=1, max_length=80)
    target_cents: PositiveCents
    deadline: date | None = None
    description: str | None = Field(default=None, max_length=1000)
    color: str = "#7c3aed"
    icon: str = "target"


class GoalOut(OutputModel):
    id: uuid.UUID
    name: str
    description: str | None
    target_cents: int
    deadline: date | None
    status: GoalStatus
    allocated_cents: int
    remaining_cents: int
    percent: Decimal
    estimated_completion: date | None
    required_monthly_cents: int | None


class AllocationIn(InputModel):
    amount_cents: PositiveCents
    allocation_date: date | None = None
    note: str | None = Field(default=None, max_length=200)


class AllocationOut(OutputModel):
    id: uuid.UUID
    amount_cents: int
    allocation_date: date
    note: str | None
    reverses_id: uuid.UUID | None
    created_at: datetime


class RecurrenceIn(InputModel):
    type: TransactionType
    account_id: uuid.UUID
    category_id: uuid.UUID
    description: str = Field(min_length=1, max_length=140)
    amount_cents: PositiveCents
    frequency: Frequency
    start_date: date
    interval: int = Field(default=1, ge=1, le=365)
    end_date: date | None = None
    auto_settle: bool = False
    notes: str | None = Field(default=None, max_length=1000)


class RecurrenceOut(OutputModel):
    id: uuid.UUID
    type: TransactionType
    account_id: uuid.UUID
    category_id: uuid.UUID
    description: str
    amount_cents: int
    frequency: Frequency
    interval: int
    start_date: date
    end_date: date | None
    status: RecurrenceStatus
    auto_settle: bool
    generated_until: date | None


class CardIn(InputModel):
    name: str = Field(min_length=1, max_length=60)
    limit_cents: PositiveCents
    closing_day: int = Field(ge=1, le=31)
    due_day: int = Field(ge=1, le=31)
    institution: str | None = Field(default=None, max_length=80)
    last4: str | None = Field(
        default=None, pattern=r"^\d{4}$", description="Somente os 4 últimos dígitos"
    )
    color: str = "#0b1b3f"


class CardOut(OutputModel):
    id: uuid.UUID
    name: str
    institution: str | None
    limit_cents: int
    closing_day: int
    due_day: int
    last4: str | None
    color: str
    archived_at: datetime | None


class CardSummaryOut(CardOut):
    debt_cents: int
    available_cents: int


class PurchaseIn(InputModel):
    category_id: uuid.UUID
    description: str = Field(min_length=1, max_length=140)
    purchase_date: date
    total_cents: PositiveCents
    installments: int = Field(default=1, ge=1, le=48)


class InstallmentOut(OutputModel):
    number: int
    kind: str
    amount_cents: int
    competence_date: date
    status: str
    statement_id: uuid.UUID


class PurchaseOut(OutputModel):
    id: uuid.UUID
    card_id: uuid.UUID
    category_id: uuid.UUID
    description: str
    purchase_date: date
    total_cents: int
    installments_count: int
    status: str
    installments: list[InstallmentOut]


class StatementOut(OutputModel):
    statement_id: uuid.UUID
    card_id: uuid.UUID
    year: int
    month: int
    closing_date: date
    due_date: date
    total_cents: int
    paid_cents: int
    remaining_cents: int
    status: str


class PaymentIn(InputModel):
    account_id: uuid.UUID
    amount_cents: PositiveCents
    payment_date: date


class PaymentOut(OutputModel):
    id: uuid.UUID
    statement_id: uuid.UUID
    account_id: uuid.UUID
    transaction_id: uuid.UUID
    amount_cents: int
    payment_date: date
    status: str


class ImportBatchOut(OutputModel):
    id: uuid.UUID
    account_id: uuid.UUID
    status: str
    display_name: str
    delimiter: str
    encoding: str
    has_header: bool
    headers: list[str]
    column_count: int
    report: dict[str, Any] | None
    created_at: datetime


class ImportRowOut(OutputModel):
    id: uuid.UUID
    row_number: int
    status: str | None
    occurred_on: date | None
    description: str | None
    amount_cents: int | None
    category_name: str | None
    external_id: str | None
    errors: list[str] | None
    selected: bool


class MappingIn(InputModel):
    date_col: int = Field(ge=0, le=49)
    description_col: int = Field(ge=0, le=49)
    amount_col: int = Field(ge=0, le=49)
    date_format: str = "dd/mm/aaaa"
    has_header: bool = True
    type_col: int | None = Field(default=None, ge=0, le=49)
    category_col: int | None = Field(default=None, ge=0, le=49)
    external_id_col: int | None = Field(default=None, ge=0, le=49)
    income_category_id: uuid.UUID | None = None
    expense_category_id: uuid.UUID | None = None


class ConfirmIn(InputModel):
    row_ids: list[uuid.UUID] = Field(max_length=100_000)


class ReportOut(OutputModel):
    key: str
    title: str
    regime: str
    period: str
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    totals: list[Any] | None
    notes: list[str]
