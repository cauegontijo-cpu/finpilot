"""Agregações financeiras compartilhadas por dashboard e relatórios (seção 15 e 17).

Regimes explícitos:
* COMPETÊNCIA — receitas/despesas pela `competence_date`; inclui parcelas de cartão na
  competência da fatura; exclui transferências e pagamentos de fatura.
* CAIXA — entradas/saídas pela `settlement_date` de lançamentos efetivados; inclui
  pagamentos de fatura (saída) e exclui as compras no cartão (evita dupla contagem).
* SALDO — saldo realizado = saldo inicial + efeitos efetivados.

Todas as consultas são agregadas no banco (sem N+1) e filtradas por `user_id`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import case, extract, func, select
from sqlalchemy.orm import Session

from app.domain.dates import Month, month_range
from app.models import Account, CardInstallment, CardPurchase, Category, CategoryKind, Transaction


@dataclass
class MonthTotals:
    month: Month
    income: int = 0
    expense: int = 0
    income_planned: int = 0
    expense_planned: int = 0
    card_expense: int = 0  # parte da despesa reconhecida via parcelas de cartão

    @property
    def net(self) -> int:
        return self.income - self.expense

    @property
    def savings_rate(self) -> float | None:
        if self.income <= 0:
            return None
        return round((self.income - self.expense) * 100 / self.income, 1)


@dataclass
class CategoryAmount:
    category_id: uuid.UUID
    name: str
    color: str
    icon: str
    settled: int = 0
    planned: int = 0


@dataclass
class CashFlow:
    inflow: int = 0
    outflow: int = 0
    card_payments: int = 0

    @property
    def net(self) -> int:
        return self.inflow - self.outflow


@dataclass
class Scope:
    """Filtros comuns do dashboard/relatórios."""

    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _installment_signed() -> Any:
    """Parcela (charge) soma; crédito de estorno (credit) subtrai."""
    return case(
        (CardInstallment.kind == "charge", CardInstallment.amount_cents),
        else_=-CardInstallment.amount_cents,
    )


def _ym(column: Any) -> tuple[Any, Any]:
    return extract("year", column).label("y"), extract("month", column).label("m")


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------------------------------------------------------------- competência
    def monthly_competence(
        self, user_id: uuid.UUID, start: Month, end: Month, scope: Scope | None = None
    ) -> list[MonthTotals]:
        scope = scope or Scope()
        result = {m: MonthTotals(month=m) for m in month_range(start, end)}
        y, m = _ym(Transaction.competence_date)
        stmt = (
            select(y, m, Transaction.type, Transaction.status, func.sum(Transaction.amount_cents))
            .where(
                Transaction.user_id == user_id,
                Transaction.type.in_(["income", "expense"]),
                Transaction.status != "cancelled",
                Transaction.competence_date >= start.first_day,
                Transaction.competence_date <= end.last_day,
            )
            .group_by("y", "m", Transaction.type, Transaction.status)
        )
        if scope.account_id:
            stmt = stmt.where(Transaction.account_id == scope.account_id)
        if scope.category_id:
            stmt = stmt.where(Transaction.category_id == scope.category_id)
        for year, month, tx_type, status, total in self.db.execute(stmt):
            bucket = result.get(Month(int(year), int(month)))
            if bucket is None:
                continue
            key = f"{tx_type.value}{'' if status.value == 'settled' else '_planned'}"
            setattr(bucket, key, getattr(bucket, key) + int(total))

        if scope.account_id is None:  # parcelas de cartão não pertencem a contas bancárias
            for month_key, total in self._card_expense_by_month(
                user_id, start, end, scope.category_id
            ).items():
                bucket = result.get(month_key)
                if bucket:
                    bucket.expense += total
                    bucket.card_expense += total
        return list(result.values())

    def _card_expense_by_month(
        self, user_id: uuid.UUID, start: Month, end: Month, category_id: uuid.UUID | None
    ) -> dict[Month, int]:
        y, m = _ym(CardInstallment.competence_date)
        stmt = (
            select(y, m, func.sum(_installment_signed()))
            .join(CardPurchase, CardPurchase.id == CardInstallment.purchase_id)
            .where(
                CardInstallment.user_id == user_id,
                CardInstallment.status == "active",
                CardInstallment.competence_date >= start.first_day,
                CardInstallment.competence_date <= end.last_day,
            )
            .group_by("y", "m")
        )
        if category_id:
            stmt = stmt.where(CardPurchase.category_id == category_id)
        return {Month(int(yy), int(mm)): int(total or 0) for yy, mm, total in self.db.execute(stmt)}

    def expenses_by_category(
        self, user_id: uuid.UUID, month: Month, scope: Scope | None = None
    ) -> list[CategoryAmount]:
        return self.by_category(
            user_id, month.first_day, month.last_day, CategoryKind.EXPENSE, scope
        )

    def by_category(
        self,
        user_id: uuid.UUID,
        start: date,
        end: date,
        kind: CategoryKind,
        scope: Scope | None = None,
    ) -> list[CategoryAmount]:
        """Totais por categoria na competência; despesas incluem parcelas de cartão."""
        scope = scope or Scope()
        amounts: dict[uuid.UUID, CategoryAmount] = {}

        def bucket(cat: Category) -> CategoryAmount:
            if cat.id not in amounts:
                amounts[cat.id] = CategoryAmount(cat.id, cat.name, cat.color, cat.icon)
            return amounts[cat.id]

        stmt = (
            select(Category, Transaction.status, func.sum(Transaction.amount_cents))
            .join(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.user_id == user_id,
                Transaction.type == kind.value,
                Transaction.status != "cancelled",
                Transaction.competence_date >= start,
                Transaction.competence_date <= end,
            )
            .group_by(Category.id, Transaction.status)
        )
        if scope.account_id:
            stmt = stmt.where(Transaction.account_id == scope.account_id)
        if scope.category_id:
            stmt = stmt.where(Transaction.category_id == scope.category_id)
        for cat, status, total in self.db.execute(stmt):
            b = bucket(cat)
            if status.value == "settled":
                b.settled += int(total)
            else:
                b.planned += int(total)

        if scope.account_id is None and kind is CategoryKind.EXPENSE:
            card_stmt = (
                select(Category, func.sum(_installment_signed()))
                .select_from(CardInstallment)
                .join(CardPurchase, CardPurchase.id == CardInstallment.purchase_id)
                .join(Category, Category.id == CardPurchase.category_id)
                .where(
                    CardInstallment.user_id == user_id,
                    CardInstallment.status == "active",
                    CardInstallment.competence_date >= start,
                    CardInstallment.competence_date <= end,
                )
                .group_by(Category.id)
            )
            if scope.category_id:
                card_stmt = card_stmt.where(CardPurchase.category_id == scope.category_id)
            for cat, total in self.db.execute(card_stmt):
                bucket(cat).settled += int(total or 0)

        return sorted(
            (a for a in amounts.values() if a.settled or a.planned),
            key=lambda a: (-a.settled, a.name),
        )

    # ---------------------------------------------------------------- caixa
    def cash_flow(
        self, user_id: uuid.UUID, start: date, end: date, scope: Scope | None = None
    ) -> CashFlow:
        scope = scope or Scope()
        types = ["income", "expense", "card_payment"]
        if scope.account_id:
            types += ["transfer_in", "transfer_out"]  # numa conta específica, transferência é caixa
        stmt = (
            select(Transaction.type, func.sum(Transaction.amount_cents))
            .where(
                Transaction.user_id == user_id,
                Transaction.status == "settled",
                Transaction.type.in_(types),
                Transaction.settlement_date >= start,
                Transaction.settlement_date <= end,
            )
            .group_by(Transaction.type)
        )
        if scope.account_id:
            stmt = stmt.where(Transaction.account_id == scope.account_id)
        flow = CashFlow()
        for tx_type, total in self.db.execute(stmt):
            amount = int(total)
            if tx_type.value in ("income", "transfer_in"):
                flow.inflow += amount
            else:
                flow.outflow += amount
                if tx_type.value == "card_payment":
                    flow.card_payments += amount
        return flow

    # ---------------------------------------------------------------- saldo
    def balance_evolution(
        self, user_id: uuid.UUID, start: Month, end: Month, scope: Scope | None = None
    ) -> list[tuple[Month, int]]:
        """Saldo realizado consolidado ao final de cada mês (regime de caixa)."""
        scope = scope or Scope()
        acc_stmt = select(Account.start_date, Account.initial_balance_cents).where(
            Account.user_id == user_id
        )
        tx_filter = [Transaction.user_id == user_id, Transaction.status == "settled"]
        if scope.account_id:
            acc_stmt = acc_stmt.where(Account.id == scope.account_id)
            tx_filter.append(Transaction.account_id == scope.account_id)
        accounts = self.db.execute(acc_stmt).all()

        signed = case(
            (Transaction.type.in_(["income", "transfer_in"]), Transaction.amount_cents),
            else_=-Transaction.amount_cents,
        )
        y, m = _ym(Transaction.settlement_date)
        before = (
            self.db.scalar(
                select(func.coalesce(func.sum(signed), 0)).where(
                    *tx_filter, Transaction.settlement_date < start.first_day
                )
            )
            or 0
        )
        monthly = {
            Month(int(yy), int(mm)): int(total)
            for yy, mm, total in self.db.execute(
                select(y, m, func.sum(signed))
                .where(
                    *tx_filter,
                    Transaction.settlement_date >= start.first_day,
                    Transaction.settlement_date <= end.last_day,
                )
                .group_by("y", "m")
            )
        }
        running = int(before)
        series: list[tuple[Month, int]] = []
        for month in month_range(start, end):
            running += monthly.get(month, 0)
            initial = sum(int(bal) for started, bal in accounts if started <= month.last_day)
            series.append((month, initial + running))
        return series

    def current_balance(self, user_id: uuid.UUID, account_id: uuid.UUID | None = None) -> int:
        stmt = select(func.coalesce(func.sum(Account.current_balance_cents), 0)).where(
            Account.user_id == user_id, Account.archived_at.is_(None)
        )
        if account_id:
            stmt = stmt.where(Account.id == account_id)
        return int(self.db.scalar(stmt) or 0)
