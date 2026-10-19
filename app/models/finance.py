"""Contas, categorias, transações e transferências."""

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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.ledger import TransactionOrigin, TransactionStatus, TransactionType
from app.models.base import Base, TimestampMixin, str_enum, user_fk, uuid_pk

MAX_CENTS = 100_000_000_000  # R$ 1 bilhão


class AccountType(StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    WALLET = "wallet"
    DIGITAL = "digital"
    OTHER = "other"


ACCOUNT_TYPE_LABELS = {
    AccountType.CHECKING: "Conta corrente",
    AccountType.SAVINGS: "Conta poupança",
    AccountType.WALLET: "Carteira física",
    AccountType.DIGITAL: "Conta digital",
    AccountType.OTHER: "Outro ativo líquido",
}


class CategoryKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_accounts_id_user"),
        Index(
            "uq_accounts_user_name_active",
            "user_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        CheckConstraint(
            f"initial_balance_cents BETWEEN -{MAX_CENTS} AND {MAX_CENTS}", name="initial_balance_range"
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 60", name="name_length"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    name: Mapped[str] = mapped_column(String(60))
    type: Mapped[AccountType] = mapped_column(str_enum(AccountType, "account_type"))
    institution: Mapped[str | None] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(7), server_default="#2563eb")
    icon: Mapped[str] = mapped_column(String(40), server_default="wallet")
    initial_balance_cents: Mapped[int] = mapped_column(server_default="0")
    current_balance_cents: Mapped[int] = mapped_column(server_default="0")
    start_date: Mapped[date] = mapped_column(Date)
    allow_negative: Mapped[bool] = mapped_column(server_default="false")
    archived_at: Mapped[datetime | None]

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_categories_id_user"),
        Index(
            "uq_categories_user_kind_name_active",
            "user_id",
            "kind",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 50", name="name_length"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    name: Mapped[str] = mapped_column(String(50))
    kind: Mapped[CategoryKind] = mapped_column(str_enum(CategoryKind, "category_kind"))
    color: Mapped[str] = mapped_column(String(7), server_default="#64748b")
    icon: Mapped[str] = mapped_column(String(40), server_default="tag")
    archived_at: Mapped[datetime | None]

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


class Transfer(TimestampMixin, Base):
    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_transfers_id_user"),
        CheckConstraint("from_account_id <> to_account_id", name="distinct_accounts"),
        CheckConstraint(f"amount_cents > 0 AND amount_cents <= {MAX_CENTS}", name="amount_positive"),
        ForeignKeyConstraint(
            ["from_account_id", "user_id"], ["accounts.id", "accounts.user_id"], name="fk_transfers_from"
        ),
        ForeignKeyConstraint(
            ["to_account_id", "user_id"], ["accounts.id", "accounts.user_id"], name="fk_transfers_to"
        ),
        Index("ix_transfers_user_date", "user_id", "transfer_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    from_account_id: Mapped[uuid.UUID]
    to_account_id: Mapped[uuid.UUID]
    amount_cents: Mapped[int]
    transfer_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(140))
    status: Mapped[TransactionStatus] = mapped_column(str_enum(TransactionStatus, "transfer_status"))
    cancelled_at: Mapped[datetime | None]

    from_account: Mapped[Account] = relationship(
        primaryjoin="foreign(Transfer.from_account_id) == Account.id",
        viewonly=True,
    )
    to_account: Mapped[Account] = relationship(
        primaryjoin="foreign(Transfer.to_account_id) == Account.id",
        viewonly=True,
    )


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(f"amount_cents > 0 AND amount_cents <= {MAX_CENTS}", name="amount_positive"),
        CheckConstraint(
            "(type IN ('income','expense')) = (category_id IS NOT NULL)", name="category_by_type"
        ),
        CheckConstraint(
            "(type IN ('transfer_in','transfer_out')) = (transfer_id IS NOT NULL)", name="transfer_by_type"
        ),
        CheckConstraint("status <> 'settled' OR settlement_date IS NOT NULL", name="settled_has_date"),
        CheckConstraint(
            "(recurrence_id IS NULL) = (occurrence_date IS NULL)", name="recurrence_occurrence"
        ),
        ForeignKeyConstraint(
            ["account_id", "user_id"], ["accounts.id", "accounts.user_id"], name="fk_transactions_account"
        ),
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            name="fk_transactions_category",
        ),
        ForeignKeyConstraint(
            ["transfer_id", "user_id"], ["transfers.id", "transfers.user_id"], name="fk_transactions_transfer"
        ),
        Index("ix_transactions_user_competence", "user_id", "competence_date"),
        Index("ix_transactions_user_settlement", "user_id", "settlement_date"),
        Index("ix_transactions_account_competence", "account_id", "competence_date"),
        Index("ix_transactions_user_category", "user_id", "category_id"),
        Index("ix_transactions_user_status_due", "user_id", "status", "due_date"),
        Index(
            "uq_transactions_recurrence_occurrence",
            "recurrence_id",
            "occurrence_date",
            unique=True,
            postgresql_where=text("recurrence_id IS NOT NULL"),
        ),
        Index(
            "uq_transactions_account_external",
            "account_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_transactions_account_fingerprint", "account_id", "import_fingerprint"),
        Index(
            "ix_transactions_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    account_id: Mapped[uuid.UUID]
    type: Mapped[TransactionType] = mapped_column(str_enum(TransactionType, "transaction_type"))
    status: Mapped[TransactionStatus] = mapped_column(str_enum(TransactionStatus, "transaction_status"))
    origin: Mapped[TransactionOrigin] = mapped_column(str_enum(TransactionOrigin, "transaction_origin"))
    description: Mapped[str] = mapped_column(String(140))
    amount_cents: Mapped[int]
    category_id: Mapped[uuid.UUID | None]
    competence_date: Mapped[date] = mapped_column(Date)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    transfer_id: Mapped[uuid.UUID | None]
    recurrence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recurrence_rules.id"))
    occurrence_date: Mapped[date | None] = mapped_column(Date)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )
    external_id: Mapped[str | None] = mapped_column(String(120))
    import_fingerprint: Mapped[str | None] = mapped_column(String(64))
    cancelled_at: Mapped[datetime | None]

    account: Mapped[Account] = relationship(
        primaryjoin="foreign(Transaction.account_id) == Account.id", viewonly=True
    )
    category: Mapped[Category | None] = relationship(
        primaryjoin="foreign(Transaction.category_id) == Category.id", viewonly=True
    )

    @property
    def is_transfer(self) -> bool:
        return self.transfer_id is not None
