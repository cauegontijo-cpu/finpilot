"""Resumo do dashboard e previsão de caixa via API (mesmos serviços das telas)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import Svc, VerifiedSession
from app.domain.dates import Month
from app.schemas.common import PROBLEM_RESPONSES
from app.services.analytics import MonthTotals, Scope
from app.services.forecast import HORIZONS

router = APIRouter(tags=["dashboard"], responses=PROBLEM_RESPONSES)


class MonthSummary(BaseModel):
    month: str
    income_cents: int
    expense_cents: int
    income_planned_cents: int
    expense_planned_cents: int
    card_expense_cents: int
    net_cents: int


class DashboardOut(BaseModel):
    month: str
    regime: dict[str, str]
    balance_cents: int
    reserved_goals_cents: int
    free_balance_cents: int
    current: MonthSummary
    history: list[MonthSummary]
    budget_used_percent: str
    open_statements_cents: int
    card_debt_cents: int
    forecast_90d_ending_cents: int
    first_negative_date: date | None


class ForecastPoint(BaseModel):
    day: date
    balance_cents: int


class ForecastEventOut(BaseModel):
    day: date
    description: str
    amount_cents: int
    source: str
    overdue: bool


class ForecastOut(BaseModel):
    start_date: date
    end_date: date
    starting_balance_cents: int
    inflow_cents: int
    outflow_cents: int
    ending_balance_cents: int
    negative_days: list[date]
    warnings: list[str]
    events: list[ForecastEventOut]
    series: list[ForecastPoint]


def _summary(m: MonthTotals) -> MonthSummary:
    return MonthSummary(
        month=m.month.iso(),
        income_cents=m.income,
        expense_cents=m.expense,
        income_planned_cents=m.income_planned,
        expense_planned_cents=m.expense_planned,
        card_expense_cents=m.card_expense,
        net_cents=m.net,
    )


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    session: VerifiedSession,
    svc: Svc,
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
    account_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
) -> DashboardOut:
    if account_id:
        svc.accounts.get(session.user.id, account_id)  # 404 se não for do usuário
    if category_id:
        svc.categories.get(session.user.id, category_id)
    d = svc.dashboard.build(session.user.id, Month(year, month), Scope(account_id=account_id, category_id=category_id))
    first_negative = d.forecast.first_negative
    return DashboardOut(
        month=d.month.iso(),
        regime={
            "income_expense": "competência",
            "balance": "saldo realizado",
            "open_statements": "caixa",
            "forecast": "projetado (determinístico)",
        },
        balance_cents=d.balance,
        reserved_goals_cents=d.reserved_goals,
        free_balance_cents=d.free_balance,
        current=_summary(d.current),
        history=[_summary(m) for m in d.history],
        budget_used_percent=str(d.budgets.pct),
        open_statements_cents=d.open_statements_total,
        card_debt_cents=d.card_debt,
        forecast_90d_ending_cents=d.forecast.ending_balance,
        first_negative_date=first_negative[0] if first_negative else None,
    )


@router.get("/dashboard/forecast", response_model=ForecastOut)
def forecast(
    session: VerifiedSession,
    svc: Svc,
    days: Annotated[int, Query(description="30, 60 ou 90")] = 30,
    account_id: uuid.UUID | None = None,
) -> ForecastOut:
    if days not in HORIZONS:
        days = 30
    if account_id:
        svc.accounts.get(session.user.id, account_id)
    f = svc.forecast.build(session.user.id, svc.clock.today(), days, account_id)
    return ForecastOut(
        start_date=f.start_date,
        end_date=f.end_date,
        starting_balance_cents=f.starting_balance,
        inflow_cents=f.inflow,
        outflow_cents=f.outflow,
        ending_balance_cents=f.ending_balance,
        negative_days=[d for d, _ in f.negative_days],
        warnings=f.warnings,
        events=[ForecastEventOut(**e.__dict__) for e in f.events],
        series=[ForecastPoint(day=d, balance_cents=b) for d, b in f.series],
    )
