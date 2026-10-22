"""Monta o modelo de visão do dashboard a partir dos serviços de cálculo compartilhados.

Nenhuma regra financeira é implementada aqui ou nos templates: apenas composição de
AnalyticsService, BudgetService, ForecastService e consultas de faturas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.clock import Clock
from app.domain.dates import Month
from app.domain.money import format_brl
from app.models import Goal, GoalAllocation, Transaction
from app.services.analytics import AnalyticsService, MonthTotals, Scope
from app.services.audit import AuditService
from app.services.budgets import BudgetOverview, BudgetService
from app.services.card_queries import StatementBalance, card_debt, statement_balances
from app.services.forecast import Forecast, ForecastService

#: Tons de cinza para séries categóricas (a cor fica reservada para alertas).
CHART_GRAYS = ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6", "--cat-7"]


@dataclass
class UpcomingItem:
    day: date
    description: str
    amount_cents: int
    kind: str  # bill | income | card
    overdue: bool
    link: str


@dataclass
class DashboardData:
    month: Month
    scope: Scope
    balance: int
    reserved_goals: int
    current: MonthTotals
    previous: MonthTotals
    history: list[MonthTotals]
    budgets: BudgetOverview
    open_statements: list[StatementBalance]
    card_debt: int
    upcoming: list[UpcomingItem]
    forecast: Forecast
    charts: dict[str, Any] = field(default_factory=dict)
    tables: dict[str, Any] = field(default_factory=dict)

    @property
    def free_balance(self) -> int:
        return self.balance - self.reserved_goals

    @property
    def open_statements_total(self) -> int:
        return sum(max(0, s.remaining_cents) for s in self.open_statements)

    @property
    def has_data(self) -> bool:
        return (
            any(
                m.income or m.expense or m.income_planned or m.expense_planned for m in self.history
            )
            or self.balance != 0
        )


class DashboardService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.analytics = AnalyticsService(db)
        self.budgets = BudgetService(db, clock, audit)
        self.forecasts = ForecastService(db)

    def reserved_in_goals(self, user_id: uuid.UUID) -> int:
        return int(
            self.db.scalar(
                select(func.coalesce(func.sum(GoalAllocation.amount_cents), 0))
                .join(Goal, Goal.id == GoalAllocation.goal_id)
                .where(GoalAllocation.user_id == user_id, Goal.status != "archived")
            )
            or 0
        )

    def upcoming(
        self, user_id: uuid.UUID, today: date, days: int = 15, account_id: uuid.UUID | None = None
    ) -> list[UpcomingItem]:
        horizon = today + timedelta(days=days)
        expected = func.coalesce(Transaction.due_date, Transaction.competence_date)
        stmt = (
            select(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.status == "planned",
                Transaction.type.in_(["expense", "income"]),
                expected <= horizon,
            )
            .order_by(expected)
            .limit(12)
        )
        if account_id:
            stmt = stmt.where(Transaction.account_id == account_id)
        items = [
            UpcomingItem(
                day=t.due_date or t.competence_date,
                description=t.description,
                amount_cents=t.amount_cents,
                kind="income" if t.type.value == "income" else "bill",
                overdue=(t.due_date or t.competence_date) < today,
                link="/app/transacoes?status=planned&period=all",
            )
            for t in self.db.scalars(stmt.options(joinedload(Transaction.account)))
        ]
        if account_id is None:
            for s in statement_balances(self.db, user_id, due_to=horizon):
                if s.remaining_cents > 0:
                    items.append(
                        UpcomingItem(
                            day=s.due_date,
                            description=f"Fatura {s.card_name}",
                            amount_cents=s.remaining_cents,
                            kind="card",
                            overdue=s.due_date < today,
                            link=f"/app/faturas/{s.statement_id}",
                        )
                    )
        return sorted(items, key=lambda i: i.day)

    def build(self, user_id: uuid.UUID, month: Month, scope: Scope) -> DashboardData:
        today = self.clock.today()
        start = month.shift(-11)
        history = self.analytics.monthly_competence(user_id, start, month, scope)
        current, previous = history[-1], history[-2]
        # Faturas em aberto: já fechadas e não quitadas + a fatura corrente (fecha em até 31 dias).
        statements = (
            [
                s
                for s in statement_balances(self.db, user_id)
                if s.remaining_cents > 0 and s.closing_date <= today + timedelta(days=31)
            ]
            if scope.account_id is None
            else []
        )
        data = DashboardData(
            month=month,
            scope=scope,
            balance=self.analytics.current_balance(user_id, scope.account_id),
            reserved_goals=self.reserved_in_goals(user_id) if scope.account_id is None else 0,
            current=current,
            previous=previous,
            history=history,
            budgets=self.budgets.overview(user_id, month),
            open_statements=statements,
            card_debt=card_debt(self.db, user_id) if scope.account_id is None else 0,
            upcoming=self.upcoming(user_id, today, account_id=scope.account_id),
            forecast=self.forecasts.build(user_id, today, 90, scope.account_id),
        )
        self._charts(user_id, data)
        return data

    # ---------------------------------------------------------------- gráficos
    def _charts(self, user_id: uuid.UUID, data: DashboardData) -> None:
        labels = [m.month.short_label() for m in data.history]
        data.charts["income_expense"] = {
            "type": "bar",
            "labels": labels,
            "datasets": [
                {
                    "label": "Receitas",
                    "data": [m.income for m in data.history],
                    "color": "--chart-1",
                },
                {
                    "label": "Despesas",
                    "data": [m.expense for m in data.history],
                    "color": "--chart-2",
                },
            ],
        }
        data.tables["income_expense"] = {
            "headers": ["Mês", "Receitas", "Despesas", "Resultado"],
            "rows": [
                [
                    m.month.short_label(),
                    format_brl(m.income),
                    format_brl(m.expense),
                    format_brl(m.net),
                ]
                for m in data.history
            ],
        }

        categories = self.analytics.expenses_by_category(user_id, data.month, data.scope)
        top = categories[:7]
        others = sum(c.settled for c in categories[7:])
        data.charts["categories"] = {
            "type": "doughnut",
            "labels": [c.name for c in top] + (["Outras"] if others else []),
            "datasets": [
                {
                    "label": "Despesas",
                    "data": [c.settled for c in top] + ([others] if others else []),
                    "color": CHART_GRAYS[: len(top)] + (["--chart-3"] if others else []),
                }
            ],
        }
        data.tables["categories"] = {
            "headers": ["Categoria", "Efetivado", "Previsto"],
            "rows": [[c.name, format_brl(c.settled), format_brl(c.planned)] for c in categories],
        }
        data.charts["categories_total"] = sum(c.settled for c in categories)

        evolution = self.analytics.balance_evolution(
            user_id, data.month.shift(-11), data.month, data.scope
        )
        data.charts["balance"] = {
            "type": "line",
            "labels": [m.short_label() for m, _ in evolution],
            "legend": False,
            "datasets": [
                {
                    "label": "Saldo consolidado",
                    "data": [v for _, v in evolution],
                    "color": "--chart-1",
                }
            ],
        }
        data.tables["balance"] = {
            "headers": ["Mês", "Saldo ao final do mês"],
            "rows": [[m.short_label(), format_brl(v)] for m, v in evolution],
        }

        items = data.budgets.items
        data.charts["budgets"] = {
            "type": "bar",
            "horizontal": True,
            "unit": "percent",
            "legend": False,
            "labels": [i.budget.category.name for i in items],
            "datasets": [
                {
                    "label": "Utilizado",
                    "data": [float(min(i.pct, Decimal(150))) for i in items],
                    "color": [
                        "negative"
                        if i.level == "over"
                        else "warning"
                        if i.level == "warning"
                        else "--chart-2"
                        for i in items
                    ],
                }
            ],
        }
        data.tables["budgets"] = {
            "headers": ["Categoria", "Planejado", "Utilizado", "%"],
            "rows": [
                [
                    i.budget.category.name,
                    format_brl(i.limit),
                    format_brl(i.used),
                    f"{i.pct}%".replace(".", ","),
                ]
                for i in items
            ],
        }

        monthly = data.forecast.monthly()
        data.charts["forecast"] = {
            "type": "bar",
            "labels": [m.short_label() for m, _, _ in monthly],
            "datasets": [
                {
                    "label": "Entradas previstas",
                    "data": [i for _, i, _ in monthly],
                    "color": "--chart-1",
                },
                {
                    "label": "Saídas previstas",
                    "data": [o for _, _, o in monthly],
                    "color": "--chart-2",
                },
            ],
        }
        data.tables["forecast"] = {
            "headers": ["Mês", "Entradas previstas", "Saídas previstas"],
            "rows": [[m.short_label(), format_brl(i), format_brl(o)] for m, i, o in monthly],
        }
