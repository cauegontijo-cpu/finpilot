"""Orçamentos mensais por categoria (seção 10, RN029–RN037).

O consumo nunca é armazenado: é calculado a cada consulta a partir das despesas
efetivadas da competência + parcelas de cartão reconhecidas no mês. Assim,
cancelamentos e estornos refletem imediatamente nos indicadores (RN036).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.clock import Clock
from app.core.errors import ConflictError, ValidationFailed
from app.domain.dates import Month
from app.domain.money import percent
from app.models import Budget, CardInstallment, CardPurchase, CategoryKind, Transaction
from app.models.finance import MAX_CENTS
from app.repositories.base import get_owned
from app.services.audit import AuditService, diff
from app.services.categories import CategoryService

WARNING_PCT = Decimal(80)
LIMIT_PCT = Decimal(100)


@dataclass
class BudgetStatus:
    budget: Budget
    used: int
    planned: int
    previous_used: int

    @property
    def limit(self) -> int:
        return self.budget.limit_cents

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    @property
    def pct(self) -> Decimal:
        return percent(self.used, self.limit)

    @property
    def planned_pct(self) -> Decimal:
        return percent(self.planned, self.limit)

    @property
    def level(self) -> str:
        """ok | warning (≥80%, RN034) | over (≥100%, RN035)."""
        if self.pct >= LIMIT_PCT:
            return "over"
        if self.pct >= WARNING_PCT:
            return "warning"
        return "ok"

    @property
    def variation_vs_previous(self) -> int:
        return self.used - self.previous_used


@dataclass
class BudgetOverview:
    month: Month
    items: list[BudgetStatus]

    @property
    def total_limit(self) -> int:
        return sum(i.limit for i in self.items)

    @property
    def total_used(self) -> int:
        return sum(i.used for i in self.items)

    @property
    def total_planned(self) -> int:
        return sum(i.planned for i in self.items)

    @property
    def pct(self) -> Decimal:
        return percent(self.total_used, self.total_limit)

    @property
    def alerts(self) -> list[BudgetStatus]:
        return [i for i in self.items if i.level != "ok"]


class BudgetService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.categories = CategoryService(db, clock, audit)

    def get(self, user_id: uuid.UUID, budget_id: str | uuid.UUID) -> Budget:
        return get_owned(self.db, Budget, budget_id, user_id, message="Orçamento não encontrado.")

    # ---------------------------------------------------------------- consumo
    def consumption(self, user_id: uuid.UUID, month: Month) -> dict[uuid.UUID, tuple[int, int]]:
        """{categoria: (efetivado, previsto)} da competência — transferências excluídas (RN032)."""
        rows = self.db.execute(
            select(
                Transaction.category_id,
                func.sum(case((Transaction.status == "settled", Transaction.amount_cents), else_=0)),
                func.sum(case((Transaction.status == "planned", Transaction.amount_cents), else_=0)),
            )
            .where(
                Transaction.user_id == user_id,
                Transaction.type == "expense",
                Transaction.status != "cancelled",
                Transaction.competence_date >= month.first_day,
                Transaction.competence_date <= month.last_day,
            )
            .group_by(Transaction.category_id)
        ).all()
        result: dict[uuid.UUID, tuple[int, int]] = {r[0]: (int(r[1]), int(r[2])) for r in rows}
        card_rows = self.db.execute(
            select(
                CardPurchase.category_id,
                func.sum(
                    case(
                        (CardInstallment.kind == "charge", CardInstallment.amount_cents),
                        else_=-CardInstallment.amount_cents,
                    )
                ),
            )
            .join(CardPurchase, CardPurchase.id == CardInstallment.purchase_id)
            .where(
                CardInstallment.user_id == user_id,
                CardInstallment.status == "active",
                CardInstallment.competence_date >= month.first_day,
                CardInstallment.competence_date <= month.last_day,
            )
            .group_by(CardPurchase.category_id)
        ).all()
        for category_id, total in card_rows:  # RN031: parcelas reconhecidas no período
            settled, planned = result.get(category_id, (0, 0))
            result[category_id] = (settled + int(total or 0), planned)
        return result

    def overview(self, user_id: uuid.UUID, month: Month) -> BudgetOverview:
        budgets = list(
            self.db.scalars(
                select(Budget)
                .options(joinedload(Budget.category))
                .where(
                    Budget.user_id == user_id,
                    Budget.year == month.year,
                    Budget.month == month.month,
                    Budget.archived_at.is_(None),
                )
            )
        )
        current = self.consumption(user_id, month)
        previous = self.consumption(user_id, month.shift(-1))
        items = [
            BudgetStatus(
                budget=b,
                used=current.get(b.category_id, (0, 0))[0],
                planned=current.get(b.category_id, (0, 0))[1],
                previous_used=previous.get(b.category_id, (0, 0))[0],
            )
            for b in budgets
        ]
        items.sort(key=lambda i: (-i.pct, i.budget.category.name))
        return BudgetOverview(month=month, items=items)

    # ---------------------------------------------------------------- escrita
    def _validate(self, user_id: uuid.UUID, category_id: str | uuid.UUID | None, limit_cents: int) -> uuid.UUID:
        if limit_cents <= 0 or limit_cents > MAX_CENTS:
            raise ValidationFailed("Informe um limite positivo.", field_errors={"limit": "Valor inválido"})
        category = self.categories.require_for_type(user_id, category_id, CategoryKind.EXPENSE)
        return category.id

    def create(self, user_id: uuid.UUID, category_id: str | uuid.UUID | None, month: Month, limit_cents: int) -> Budget:
        cat_id = self._validate(user_id, category_id, limit_cents)  # RN029
        budget = Budget(user_id=user_id, category_id=cat_id, year=month.year, month=month.month, limit_cents=limit_cents)
        self.db.add(budget)
        self._flush_unique()
        self.audit.record(
            "budget.created",
            user_id=user_id,
            entity_type="budget",
            entity_id=budget.id,
            changes={"category_id": str(cat_id), "competencia": month.iso(), "limit_cents": limit_cents},
        )
        self.db.commit()
        return budget

    def update(self, user_id: uuid.UUID, budget_id: str, limit_cents: int) -> Budget:
        budget = self.get(user_id, budget_id)
        self._validate(user_id, budget.category_id, limit_cents)
        before = budget.limit_cents
        budget.limit_cents = limit_cents
        self.audit.record(
            "budget.updated",
            user_id=user_id,
            entity_type="budget",
            entity_id=budget.id,
            changes=diff({"limit_cents": before}, {"limit_cents": limit_cents}),
        )
        self.db.commit()
        return budget

    def archive(self, user_id: uuid.UUID, budget_id: str) -> None:
        budget = self.get(user_id, budget_id)
        budget.archived_at = self.clock.now()
        self.audit.record("budget.archived", user_id=user_id, entity_type="budget", entity_id=budget.id)
        self.db.commit()

    def copy_from_previous(self, user_id: uuid.UUID, month: Month) -> int:
        """Replica os orçamentos ativos do mês anterior que ainda não existem no mês atual."""
        previous = self.overview(user_id, month.shift(-1)).items
        existing = {b.budget.category_id for b in self.overview(user_id, month).items}
        created = 0
        for item in previous:
            if item.budget.category_id in existing or not item.budget.category.is_active:
                continue
            self.db.add(
                Budget(
                    user_id=user_id,
                    category_id=item.budget.category_id,
                    year=month.year,
                    month=month.month,
                    limit_cents=item.budget.limit_cents,
                )
            )
            created += 1
        if created:
            self._flush_unique()
            self.audit.record(
                "budget.copied", user_id=user_id, entity_type="budget", changes={"competencia": month.iso(), "quantidade": created}
            )
            self.db.commit()
        return created

    def _flush_unique(self) -> None:
        # Flush direto: em caso de violação, a transação inteira é desfeita (a operação falhou).
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError(
                "Já existe um orçamento ativo para essa categoria nesta competência.",  # RN030
                code="RN030",
                field_errors={"category_id": "Orçamento já existe"},
            ) from exc
