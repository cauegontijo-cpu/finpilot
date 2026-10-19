"""Consultas agregadas de faturas de cartão, reutilizadas por cartões, dashboard e previsão."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import CardInstallment, CardPayment, CardStatement, CreditCard


@dataclass(frozen=True)
class StatementBalance:
    statement_id: uuid.UUID
    card_id: uuid.UUID
    card_name: str
    year: int
    month: int
    closing_date: date
    due_date: date
    total_cents: int
    paid_cents: int

    @property
    def remaining_cents(self) -> int:
        return self.total_cents - self.paid_cents

    def status(self, today: date) -> str:
        """open (aberta) | closed (fechada, a pagar) | partial | paid | overdue | credit."""
        if self.total_cents <= 0 and self.paid_cents == 0:
            return "open" if today <= self.closing_date else "paid"
        if self.remaining_cents <= 0:
            return "paid"
        if today <= self.closing_date:
            return "open"
        if today > self.due_date:
            return "overdue"
        return "partial" if self.paid_cents > 0 else "closed"


def statement_balances(
    db: Session,
    user_id: uuid.UUID,
    *,
    card_id: uuid.UUID | None = None,
    statement_ids: list[uuid.UUID] | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
) -> list[StatementBalance]:
    totals = (
        select(
            CardInstallment.statement_id.label("sid"),
            func.sum(
                case(
                    (CardInstallment.kind == "charge", CardInstallment.amount_cents),
                    else_=-CardInstallment.amount_cents,
                )
            ).label("total"),
        )
        .where(CardInstallment.user_id == user_id, CardInstallment.status == "active")
        .group_by(CardInstallment.statement_id)
        .subquery()
    )
    paid = (
        select(
            CardPayment.statement_id.label("sid"), func.sum(CardPayment.amount_cents).label("paid")
        )
        .where(CardPayment.user_id == user_id, CardPayment.status == "active")
        .group_by(CardPayment.statement_id)
        .subquery()
    )
    stmt = (
        select(
            CardStatement,
            CreditCard.name,
            func.coalesce(totals.c.total, 0),
            func.coalesce(paid.c.paid, 0),
        )
        .join(CreditCard, CreditCard.id == CardStatement.card_id)
        .outerjoin(totals, totals.c.sid == CardStatement.id)
        .outerjoin(paid, paid.c.sid == CardStatement.id)
        .where(CardStatement.user_id == user_id)
        .order_by(CardStatement.due_date)
    )
    if card_id:
        stmt = stmt.where(CardStatement.card_id == card_id)
    if statement_ids is not None:
        stmt = stmt.where(CardStatement.id.in_(statement_ids))
    if due_from:
        stmt = stmt.where(CardStatement.due_date >= due_from)
    if due_to:
        stmt = stmt.where(CardStatement.due_date <= due_to)
    return [
        StatementBalance(
            statement_id=s.id,
            card_id=s.card_id,
            card_name=name,
            year=s.year,
            month=s.month,
            closing_date=s.closing_date,
            due_date=s.due_date,
            total_cents=int(total),
            paid_cents=int(paid_amount),
        )
        for s, name, total, paid_amount in db.execute(stmt)
    ]


def card_debt(db: Session, user_id: uuid.UUID, card_id: uuid.UUID | None = None) -> int:
    """Dívida total do cartão = parcelas ativas (líquidas de créditos) − pagamentos ativos."""
    charges_stmt = select(
        func.coalesce(
            func.sum(
                case(
                    (CardInstallment.kind == "charge", CardInstallment.amount_cents),
                    else_=-CardInstallment.amount_cents,
                )
            ),
            0,
        )
    ).where(CardInstallment.user_id == user_id, CardInstallment.status == "active")
    paid_stmt = select(func.coalesce(func.sum(CardPayment.amount_cents), 0)).where(
        CardPayment.user_id == user_id, CardPayment.status == "active"
    )
    if card_id:
        charges_stmt = charges_stmt.join(
            CardStatement, CardStatement.id == CardInstallment.statement_id
        ).where(CardStatement.card_id == card_id)
        paid_stmt = paid_stmt.join(
            CardStatement, CardStatement.id == CardPayment.statement_id
        ).where(CardStatement.card_id == card_id)
    return int(db.scalar(charges_stmt) or 0) - int(db.scalar(paid_stmt) or 0)
