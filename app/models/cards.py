"""Cartões de crédito, faturas, compras, parcelas e pagamentos (RN061–RN070).

Nunca armazenamos número completo do cartão, CVV ou senha — apenas os 4 últimos dígitos.
"""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, str_enum, user_fk, uuid_pk
from app.models.finance import MAX_CENTS, Category


class CreditCard(TimestampMixin, Base):
    __tablename__ = "credit_cards"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_credit_cards_id_user"),
        CheckConstraint(f"limit_cents > 0 AND limit_cents <= {MAX_CENTS}", name="limit_positive"),
        CheckConstraint("closing_day BETWEEN 1 AND 31", name="closing_day_range"),
        CheckConstraint("due_day BETWEEN 1 AND 31", name="due_day_range"),
        CheckConstraint("last4 IS NULL OR last4 ~ '^[0-9]{4}$'", name="last4_digits"),
        CheckConstraint("length(name) BETWEEN 1 AND 60", name="name_length"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    name: Mapped[str] = mapped_column(String(60))
    institution: Mapped[str | None] = mapped_column(String(80))
    limit_cents: Mapped[int]
    closing_day: Mapped[int] = mapped_column(SmallInteger)
    due_day: Mapped[int] = mapped_column(SmallInteger)
    last4: Mapped[str | None] = mapped_column(String(4))
    color: Mapped[str] = mapped_column(String(7), server_default="#0b1b3f")
    archived_at: Mapped[datetime | None]

    @property
    def is_active(self) -> bool:
        return self.archived_at is None


class CardStatement(TimestampMixin, Base):
    __tablename__ = "card_statements"
    __table_args__ = (
        UniqueConstraint("card_id", "year", "month", name="uq_card_statements_card_period"),
        UniqueConstraint("id", "user_id", name="uq_card_statements_id_user"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month_range"),
        CheckConstraint("due_date >= closing_date", name="due_after_closing"),
        ForeignKeyConstraint(
            ["card_id", "user_id"],
            ["credit_cards.id", "credit_cards.user_id"],
            name="fk_card_statements_card",
        ),
        Index("ix_card_statements_user_due", "user_id", "due_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    card_id: Mapped[uuid.UUID]
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    closing_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)

    card: Mapped[CreditCard] = relationship(
        primaryjoin="foreign(CardStatement.card_id) == CreditCard.id", viewonly=True
    )


class PurchaseStatus(StrEnum):
    ACTIVE = "active"
    REFUNDED = "refunded"


class CardPurchase(TimestampMixin, Base):
    __tablename__ = "card_purchases"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_card_purchases_id_user"),
        CheckConstraint(f"total_cents > 0 AND total_cents <= {MAX_CENTS}", name="total_positive"),
        CheckConstraint("installments_count BETWEEN 1 AND 48", name="installments_range"),
        CheckConstraint("total_cents >= installments_count", name="min_installment_cent"),
        ForeignKeyConstraint(
            ["card_id", "user_id"],
            ["credit_cards.id", "credit_cards.user_id"],
            name="fk_card_purchases_card",
        ),
        ForeignKeyConstraint(
            ["category_id", "user_id"],
            ["categories.id", "categories.user_id"],
            name="fk_card_purchases_category",
        ),
        Index("ix_card_purchases_card_date", "card_id", "purchase_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    card_id: Mapped[uuid.UUID]
    category_id: Mapped[uuid.UUID]
    description: Mapped[str] = mapped_column(String(140))
    purchase_date: Mapped[date] = mapped_column(Date)
    total_cents: Mapped[int]
    installments_count: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[PurchaseStatus] = mapped_column(str_enum(PurchaseStatus, "purchase_status"))
    refunded_at: Mapped[datetime | None]

    category: Mapped[Category] = relationship(
        primaryjoin="foreign(CardPurchase.category_id) == Category.id", viewonly=True
    )
    card: Mapped[CreditCard] = relationship(
        primaryjoin="foreign(CardPurchase.card_id) == CreditCard.id", viewonly=True
    )
    installments: Mapped[list[CardInstallment]] = relationship(
        back_populates="purchase", order_by="CardInstallment.number"
    )


class InstallmentKind(StrEnum):
    CHARGE = "charge"
    CREDIT = "credit"  # estorno de parcelas já faturadas


class InstallmentStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class CardInstallment(Base):
    """Parcela reconhecida como despesa na competência da fatura a que pertence."""

    __tablename__ = "card_installments"
    __table_args__ = (
        UniqueConstraint(
            "purchase_id", "number", "kind", name="uq_card_installments_purchase_number"
        ),
        CheckConstraint(
            f"amount_cents > 0 AND amount_cents <= {MAX_CENTS}", name="amount_positive"
        ),
        CheckConstraint("number >= 1", name="number_positive"),
        ForeignKeyConstraint(
            ["statement_id", "user_id"],
            ["card_statements.id", "card_statements.user_id"],
            name="fk_card_installments_statement",
        ),
        ForeignKeyConstraint(
            ["purchase_id", "user_id"],
            ["card_purchases.id", "card_purchases.user_id"],
            name="fk_card_installments_purchase",
        ),
        Index("ix_card_installments_user_competence", "user_id", "competence_date"),
        Index("ix_card_installments_statement", "statement_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    purchase_id: Mapped[uuid.UUID]
    statement_id: Mapped[uuid.UUID]
    number: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[InstallmentKind] = mapped_column(str_enum(InstallmentKind, "installment_kind"))
    amount_cents: Mapped[int]
    competence_date: Mapped[date] = mapped_column(Date)
    status: Mapped[InstallmentStatus] = mapped_column(
        str_enum(InstallmentStatus, "installment_status")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    purchase: Mapped[CardPurchase] = relationship(
        back_populates="installments",
        primaryjoin="foreign(CardInstallment.purchase_id) == CardPurchase.id",
    )
    statement: Mapped[CardStatement] = relationship(
        primaryjoin="foreign(CardInstallment.statement_id) == CardStatement.id",
        viewonly=True,
    )

    @property
    def signed_cents(self) -> int:
        return self.amount_cents if self.kind is InstallmentKind.CHARGE else -self.amount_cents


class PaymentStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


class CardPayment(TimestampMixin, Base):
    __tablename__ = "card_payments"
    __table_args__ = (
        CheckConstraint(
            f"amount_cents > 0 AND amount_cents <= {MAX_CENTS}", name="amount_positive"
        ),
        ForeignKeyConstraint(
            ["statement_id", "user_id"],
            ["card_statements.id", "card_statements.user_id"],
            name="fk_card_payments_statement",
        ),
        ForeignKeyConstraint(
            ["account_id", "user_id"],
            ["accounts.id", "accounts.user_id"],
            name="fk_card_payments_account",
        ),
        Index("ix_card_payments_statement", "statement_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    statement_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"), unique=True)
    amount_cents: Mapped[int]
    payment_date: Mapped[date] = mapped_column(Date)
    status: Mapped[PaymentStatus] = mapped_column(str_enum(PaymentStatus, "payment_status"))
    cancelled_at: Mapped[datetime | None]
