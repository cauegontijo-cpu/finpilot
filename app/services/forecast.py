"""Previsão determinística de fluxo de caixa (seção 16, RN071–RN077).

* Parte do saldo REALIZADO atual (RN071).
* Considera somente eventos conhecidos: lançamentos previstos, ocorrências futuras de
  recorrências ativas e faturas de cartão a pagar — nada de estatística ou IA (RN077).
* Entradas previstas nunca se misturam às realizadas (RN072); despesas entram na data
  esperada do desembolso: vencimento, ou competência na ausência dele (RN073).
* Transferências entre contas próprias não alteram o caixa consolidado (RN074).
* Faturas entram como saída na data de vencimento pelo saldo devido; compras no cartão
  não são somadas como saída bancária (RN075).
* Ausência de dados futuros é reportada como incerteza (RN076).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.dates import Month
from app.domain.recurrence import occurrences_between
from app.models import Account, RecurrenceRule, Transaction
from app.services.card_queries import statement_balances

HORIZONS = (30, 60, 90)


@dataclass(frozen=True)
class ForecastEvent:
    day: date
    description: str
    amount_cents: int  # com sinal: + entrada, − saída
    source: str  # planned | recurrence | card | overdue
    overdue: bool = False


@dataclass
class Forecast:
    start_date: date
    end_date: date
    starting_balance: int
    events: list[ForecastEvent] = field(default_factory=list)
    series: list[tuple[date, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def inflow(self) -> int:
        return sum(e.amount_cents for e in self.events if e.amount_cents > 0)

    @property
    def outflow(self) -> int:
        return -sum(e.amount_cents for e in self.events if e.amount_cents < 0)

    @property
    def ending_balance(self) -> int:
        return self.series[-1][1] if self.series else self.starting_balance

    @property
    def lowest(self) -> tuple[date, int]:
        return min(self.series, key=lambda p: (p[1], p[0])) if self.series else (self.start_date, self.starting_balance)

    @property
    def negative_days(self) -> list[tuple[date, int]]:
        return [(d, b) for d, b in self.series if b < 0]

    @property
    def first_negative(self) -> tuple[date, int, list[ForecastEvent]] | None:
        for d, b in self.series:
            if b < 0:
                return d, b, [e for e in self.events if e.day == d and e.amount_cents < 0]
        return None

    def monthly(self) -> list[tuple[Month, int, int]]:
        """(mês, entradas, saídas) previstas por mês."""
        buckets: dict[Month, list[int]] = {}
        for e in self.events:
            b = buckets.setdefault(Month.of(e.day), [0, 0])
            if e.amount_cents > 0:
                b[0] += e.amount_cents
            else:
                b[1] += -e.amount_cents
        return [(m, v[0], v[1]) for m, v in sorted(buckets.items())]


class ForecastService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build(self, user_id: uuid.UUID, today: date, days: int = 30, account_id: uuid.UUID | None = None) -> Forecast:
        end = today + timedelta(days=days)
        accounts_stmt = select(func.coalesce(func.sum(Account.current_balance_cents), 0)).where(
            Account.user_id == user_id, Account.archived_at.is_(None)
        )
        if account_id:
            accounts_stmt = accounts_stmt.where(Account.id == account_id)
        forecast = Forecast(start_date=today, end_date=end, starting_balance=int(self.db.scalar(accounts_stmt) or 0))
        events: list[ForecastEvent] = []
        events += self._planned(user_id, today, end, account_id)
        events += self._recurrences(user_id, today, end, account_id)
        if account_id is None:
            events += self._cards(user_id, today, end)
        else:
            forecast.warnings.append(
                "Faturas de cartão não são atribuídas a uma conta específica e não entram nesta projeção filtrada."
            )
        events.sort(key=lambda e: (e.day, e.amount_cents))
        forecast.events = events

        by_day: dict[date, int] = {}
        for e in events:
            by_day[e.day] = by_day.get(e.day, 0) + e.amount_cents
        balance = forecast.starting_balance
        current = today
        while current <= end:
            balance += by_day.get(current, 0)
            forecast.series.append((current, balance))
            current += timedelta(days=1)

        if not events:
            forecast.warnings.append(
                "Não há lançamentos futuros conhecidos neste período: a linha constante não é uma garantia."
            )
        else:
            last_known = max(e.day for e in events)
            if (end - last_known).days > 20:
                forecast.warnings.append(
                    f"Não há eventos conhecidos após {last_known:%d/%m/%Y}; o restante do período reflete apenas ausência de dados."
                )
        if any(e.overdue for e in events):
            forecast.warnings.append("Há valores em atraso considerados como devidos hoje.")
        return forecast

    def _planned(self, user_id: uuid.UUID, today: date, end: date, account_id: uuid.UUID | None) -> list[ForecastEvent]:
        expected = func.coalesce(Transaction.due_date, Transaction.competence_date)
        types = ["income", "expense"] + (["transfer_in", "transfer_out"] if account_id else [])
        stmt = select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.status == "planned",
            Transaction.type.in_(types),
            expected <= end,
        )
        if account_id:
            stmt = stmt.where(Transaction.account_id == account_id)
        events = []
        for tx in self.db.scalars(stmt):
            when = tx.due_date or tx.competence_date
            overdue = when < today
            sign = 1 if tx.type.value in ("income", "transfer_in") else -1
            events.append(
                ForecastEvent(
                    day=today if overdue else when,
                    description=tx.description,
                    amount_cents=sign * tx.amount_cents,
                    source="overdue" if overdue else "planned",
                    overdue=overdue,
                )
            )
        return events

    def _recurrences(self, user_id: uuid.UUID, today: date, end: date, account_id: uuid.UUID | None) -> list[ForecastEvent]:
        """Ocorrências ainda não materializadas pelo gerador (as já geradas entram como previstas)."""
        stmt = select(RecurrenceRule).where(RecurrenceRule.user_id == user_id, RecurrenceRule.status == "active")
        if account_id:
            stmt = stmt.where(RecurrenceRule.account_id == account_id)
        events = []
        for rule in self.db.scalars(stmt):
            window_start = max(today + timedelta(days=1), (rule.generated_until or rule.start_date - timedelta(days=1)) + timedelta(days=1))
            sign = 1 if rule.type.value == "income" else -1
            for day in occurrences_between(rule.start_date, rule.frequency, rule.interval, window_start, end, rule.end_date):
                events.append(ForecastEvent(day=day, description=rule.description, amount_cents=sign * rule.amount_cents, source="recurrence"))
        return events

    def _cards(self, user_id: uuid.UUID, today: date, end: date) -> list[ForecastEvent]:
        events = []
        for s in statement_balances(self.db, user_id, due_to=end):
            if s.remaining_cents <= 0:
                continue
            overdue = s.due_date < today
            events.append(
                ForecastEvent(
                    day=today if overdue else s.due_date,
                    description=f"Fatura {s.card_name} ({s.month:02d}/{s.year})",
                    amount_cents=-s.remaining_cents,
                    source="overdue" if overdue else "card",
                    overdue=overdue,
                )
            )
        return events
