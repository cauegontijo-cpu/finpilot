"""Orçamentos, metas e recorrências."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.ledger import TransactionType
from app.domain.recurrence import Frequency
from app.models.base import Base, TimestampMixin, str_enum, user_fk, uuid_pk
from app.models.finance import MAX_CENTS, Account, Category


class Budget(TimestampMixin, Base):
    __tablename__ = "budgets"
    __table_args__ = (
        Index(
            "uq_budgets_user_category_period_active",
            "user_id",
            "category_id",
            "year",
            "month",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        CheckConstraint("month BETWEEN 1 AND 12", name="month_range"),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="year_range"),
        CheckConstraint(f"limit_cents > 0 AND limit_cents <= {MAX_CENTS}", name="limit_positive"),
        ForeignKeyConstraint(
            ["category_id", "user_id"], ["categories.id", "categories.user_id"], name="fk_budgets_category"
        ),
        Index("ix_budgets_user_period", "user_id", "year", "month"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    category_id: Mapped[uuid.UUID]
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    limit_cents: Mapped[int]
    archived_at: Mapped[datetime | None]

    category: Mapped[Category] = relationship(
        primaryjoin="foreign(Budget.category_id) == Category.id", viewonly=True
    )


class GoalStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


GOAL_STATUS_LABELS = {
    GoalStatus.ACTIVE: "Em andamento",
    GoalStatus.COMPLETED: "Concluída",
    GoalStatus.ARCHIVED: "Arquivada",
}


class Goal(TimestampMixin, Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(f"target_cents > 0 AND target_cents <= {MAX_CENTS}", name="target_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 80", name="name_length"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    target_cents: Mapped[int]
    deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[GoalStatus] = mapped_column(str_enum(GoalStatus, "goal_status"))
    color: Mapped[str] = mapped_column(String(7), server_default="#7c3aed")
    icon: Mapped[str] = mapped_column(String(40), server_default="target")


class GoalAllocation(Base):
    """Reserva virtual (RN039/RN040): não movimenta contas bancárias."""

    __tablename__ = "goal_allocations"
    __table_args__ = (
        CheckConstraint(
            f"amount_cents <> 0 AND abs(amount_cents) <= {MAX_CENTS}", name="amount_nonzero"
        ),
        CheckConstraint("(reverses_id IS NULL) OR (amount_cents < 0)", name="reversal_negative"),
        Index("ix_goal_allocations_goal_date", "goal_id", "allocation_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    goal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"))
    amount_cents: Mapped[int]
    allocation_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(200))
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goal_allocations.id"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RecurrenceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


RECURRENCE_STATUS_LABELS = {
    RecurrenceStatus.ACTIVE: "Ativa",
    RecurrenceStatus.PAUSED: "Pausada",
    RecurrenceStatus.CANCELLED: "Cancelada",
}


class RecurrenceRule(TimestampMixin, Base):
    __tablename__ = "recurrence_rules"
    __table_args__ = (
        CheckConstraint(f"amount_cents > 0 AND amount_cents <= {MAX_CENTS}", name="amount_positive"),
        CheckConstraint("interval BETWEEN 1 AND 365", name="interval_range"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="end_after_start"),
        CheckConstraint("type IN ('income','expense')", name="type_allowed"),
        ForeignKeyConstraint(
            ["account_id", "user_id"], ["accounts.id", "accounts.user_id"], name="fk_recurrences_account"
        ),
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            name="fk_recurrences_category",
        ),
        Index("ix_recurrence_rules_status", "status", "generated_until"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    account_id: Mapped[uuid.UUID]
    category_id: Mapped[uuid.UUID]
    type: Mapped[TransactionType] = mapped_column(str_enum(TransactionType, "recurrence_type"))
    description: Mapped[str] = mapped_column(String(140))
    amount_cents: Mapped[int]
    frequency: Mapped[Frequency] = mapped_column(str_enum(Frequency, "recurrence_frequency"))
    interval: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[RecurrenceStatus] = mapped_column(str_enum(RecurrenceStatus, "recurrence_status"))
    auto_settle: Mapped[bool] = mapped_column(server_default="false")
    generated_until: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    account: Mapped[Account] = relationship(
        primaryjoin="foreign(RecurrenceRule.account_id) == Account.id", viewonly=True
    )
    category: Mapped[Category] = relationship(
        primaryjoin="foreign(RecurrenceRule.category_id) == Category.id",
        viewonly=True,
    )
