"""Relatórios analíticos (seção 17) construídos sobre os MESMOS cálculos do dashboard.

Cada relatório produz um `ReportResult` neutro de apresentação, renderizado em HTML,
CSV ou PDF pelos exportadores (app/services/exporters.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.clock import Clock
from app.core.errors import ValidationFailed
from app.domain.dates import Month, month_range
from app.domain.ledger import STATUS_LABELS, TYPE_LABELS
from app.domain.money import format_brl, percent
from app.domain.recurrence import FREQUENCY_LABELS
from app.models import Account, CategoryKind, RecurrenceRule, Transaction
from app.models.planning import RECURRENCE_STATUS_LABELS
from app.services.analytics import AnalyticsService, Scope
from app.services.audit import AuditService
from app.services.budgets import BudgetService
from app.services.card_queries import statement_balances
from app.services.recurrences import RecurrenceService

MAX_MONTHS = 24


@dataclass(frozen=True)
class Column:
    label: str
    kind: str = "text"  # text | money | date | pct | int


@dataclass
class ReportResult:
    key: str
    title: str
    regime: str
    period_label: str
    columns: list[Column]
    rows: list[list[Any]] = field(default_factory=list)
    totals: list[Any] | None = None
    notes: list[str] = field(default_factory=list)
    chart: dict[str, Any] | None = None


REPORTS: dict[str, tuple[str, str]] = {
    "receitas-despesas": ("Receitas e despesas por período", "Competência"),
    "categorias": ("Gastos por categoria", "Competência"),
    "patrimonio": ("Evolução patrimonial das contas", "Saldo realizado (caixa)"),
    "comparativo": ("Comparativo de meses", "Competência"),
    "orcamento": ("Orçamento planejado × realizado", "Competência"),
    "movimentacoes": ("Movimentações por conta", "Caixa (data de efetivação)"),
    "recorrencias": ("Transações recorrentes", "Previsto"),
    "cartoes": ("Histórico de cartões e faturas", "Caixa (pagamentos) e competência (parcelas)"),
}


@dataclass(frozen=True)
class ReportParams:
    start: Month
    end: Month
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    compare_a: Month | None = None
    compare_b: Month | None = None

    def validate(self) -> ReportParams:
        if self.start > self.end:
            raise ValidationFailed(
                "O mês inicial deve ser anterior ao final.",
                field_errors={"start": "Período inválido"},
            )
        if len(month_range(self.start, self.end)) > MAX_MONTHS:
            raise ValidationFailed(
                f"Período máximo de {MAX_MONTHS} meses.",
                field_errors={"start": "Período longo demais"},
            )
        return self

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.start.label()
        return f"{self.start.short_label()} a {self.end.short_label()}"


class ReportService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.analytics = AnalyticsService(db)

    def build(
        self, user_id: uuid.UUID, key: str, params: ReportParams, *, limit: int | None = None
    ) -> ReportResult:
        if key not in REPORTS:
            raise ValidationFailed("Relatório inexistente.", code="invalid_report")
        params = params.validate()
        builder = getattr(self, "_" + key.replace("-", "_"))
        result: ReportResult = builder(user_id, params, limit)
        return result

    def _base(self, key: str, params: ReportParams, columns: list[Column]) -> ReportResult:
        title, regime = REPORTS[key]
        return ReportResult(
            key=key, title=title, regime=regime, period_label=params.label, columns=columns
        )

    # ---------------------------------------------------------------- relatórios
    def _receitas_despesas(
        self, user_id: uuid.UUID, p: ReportParams, _limit: int | None
    ) -> ReportResult:
        r = self._base(
            "receitas-despesas",
            p,
            [
                Column("Mês"),
                Column("Receitas", "money"),
                Column("Despesas", "money"),
                Column("Resultado", "money"),
                Column("Economia", "pct"),
                Column("Receitas previstas", "money"),
                Column("Despesas previstas", "money"),
            ],
        )
        months = self.analytics.monthly_competence(
            user_id, p.start, p.end, Scope(p.account_id, p.category_id)
        )
        for m in months:
            r.rows.append(
                [
                    m.month.short_label(),
                    m.income,
                    m.expense,
                    m.net,
                    percent(m.net, m.income) if m.income else None,
                    m.income_planned,
                    m.expense_planned,
                ]
            )
        income, expense = sum(m.income for m in months), sum(m.expense for m in months)
        r.totals = [
            "Total",
            income,
            expense,
            income - expense,
            percent(income - expense, income) if income else None,
            sum(m.income_planned for m in months),
            sum(m.expense_planned for m in months),
        ]
        r.notes.append(
            "Transferências entre contas e pagamentos de fatura não são receitas nem despesas. Parcelas de cartão entram na competência da fatura."
        )
        r.chart = {
            "type": "bar",
            "labels": [m.month.short_label() for m in months],
            "datasets": [
                {"label": "Receitas", "data": [m.income for m in months], "color": "positive"},
                {"label": "Despesas", "data": [m.expense for m in months], "color": "negative"},
            ],
        }
        return r

    def _categorias(self, user_id: uuid.UUID, p: ReportParams, _limit: int | None) -> ReportResult:
        r = self._base(
            "categorias",
            p,
            [
                Column("Categoria"),
                Column("Tipo"),
                Column("Efetivado", "money"),
                Column("Previsto", "money"),
                Column("% do total", "pct"),
            ],
        )
        scope = Scope(p.account_id, p.category_id)
        for kind, label in ((CategoryKind.EXPENSE, "Despesa"), (CategoryKind.INCOME, "Receita")):
            items = self.analytics.by_category(
                user_id, p.start.first_day, p.end.last_day, kind, scope
            )
            total = sum(i.settled for i in items)
            for i in items:
                r.rows.append([i.name, label, i.settled, i.planned, percent(i.settled, total)])
            if kind is CategoryKind.EXPENSE:
                r.chart = {
                    "type": "doughnut",
                    "labels": [i.name for i in items[:8]],
                    "datasets": [
                        {
                            "label": "Despesas",
                            "data": [i.settled for i in items[:8]],
                            "color": [i.color for i in items[:8]],
                        }
                    ],
                }
        return r

    def _patrimonio(self, user_id: uuid.UUID, p: ReportParams, _limit: int | None) -> ReportResult:
        accounts = list(
            self.db.scalars(
                select(Account).where(Account.user_id == user_id).order_by(Account.name)
            )
        )
        if p.account_id:
            accounts = [a for a in accounts if a.id == p.account_id]
        r = self._base(
            "patrimonio",
            p,
            [Column("Mês")]
            + [Column(a.name, "money") for a in accounts]
            + [Column("Consolidado", "money")],
        )
        series = {
            a.id: dict(
                self.analytics.balance_evolution(user_id, p.start, p.end, Scope(account_id=a.id))
            )
            for a in accounts
        }
        months = month_range(p.start, p.end)
        for m in months:
            values = [series[a.id][m] for a in accounts]
            r.rows.append([m.short_label(), *values, sum(values)])
        r.notes.append(
            "Saldo ao final de cada mês, considerando apenas lançamentos efetivados (inclui contas arquivadas)."
        )
        r.chart = {
            "type": "line",
            "labels": [m.short_label() for m in months],
            "legend": False,
            "datasets": [
                {"label": "Consolidado", "data": [row[-1] for row in r.rows], "color": "primary"}
            ],
        }
        return r

    def _comparativo(self, user_id: uuid.UUID, p: ReportParams, _limit: int | None) -> ReportResult:
        a = p.compare_a or p.end.shift(-1)
        b = p.compare_b or p.end
        r = self._base(
            "comparativo",
            p,
            [
                Column("Categoria"),
                Column(a.short_label(), "money"),
                Column(b.short_label(), "money"),
                Column("Variação", "money"),
                Column("Variação %", "pct"),
            ],
        )
        r.period_label = f"{a.label()} × {b.label()}"
        scope = Scope(p.account_id, p.category_id)
        first = {c.name: c.settled for c in self.analytics.expenses_by_category(user_id, a, scope)}
        second = {c.name: c.settled for c in self.analytics.expenses_by_category(user_id, b, scope)}
        for name in sorted(set(first) | set(second), key=lambda n: -(second.get(n, 0))):
            va, vb = first.get(name, 0), second.get(name, 0)
            r.rows.append([name, va, vb, vb - va, percent(vb - va, va) if va else None])
        ta, tb = sum(first.values()), sum(second.values())
        r.totals = ["Total de despesas", ta, tb, tb - ta, percent(tb - ta, ta) if ta else None]
        return r

    def _orcamento(self, user_id: uuid.UUID, p: ReportParams, _limit: int | None) -> ReportResult:
        r = self._base(
            "orcamento",
            p,
            [
                Column("Mês"),
                Column("Categoria"),
                Column("Planejado", "money"),
                Column("Realizado", "money"),
                Column("Diferença", "money"),
                Column("% utilizado", "pct"),
                Column("Situação"),
            ],
        )
        budgets = BudgetService(self.db, self.clock, self.audit)
        labels = {"ok": "Dentro do limite", "warning": "Acima de 80%", "over": "Limite atingido"}
        planned = used = 0
        for m in month_range(p.start, p.end):
            for i in budgets.overview(user_id, m).items:
                if p.category_id and i.budget.category_id != p.category_id:
                    continue
                r.rows.append(
                    [
                        m.short_label(),
                        i.budget.category.name,
                        i.limit,
                        i.used,
                        i.remaining,
                        i.pct,
                        labels[i.level],
                    ]
                )
                planned += i.limit
                used += i.used
        r.totals = [
            "Total",
            "",
            planned,
            used,
            planned - used,
            percent(used, planned) if planned else None,
            "",
        ]
        return r

    def _movimentacoes(
        self, user_id: uuid.UUID, p: ReportParams, limit: int | None
    ) -> ReportResult:
        r = self._base(
            "movimentacoes",
            p,
            [
                Column("Efetivação", "date"),
                Column("Conta"),
                Column("Descrição"),
                Column("Tipo"),
                Column("Categoria"),
                Column("Status"),
                Column("Valor", "money"),
            ],
        )
        stmt = (
            select(Transaction)
            .options(joinedload(Transaction.account), joinedload(Transaction.category))
            .where(
                Transaction.user_id == user_id,
                Transaction.status == "settled",
                Transaction.settlement_date >= p.start.first_day,
                Transaction.settlement_date <= p.end.last_day,
            )
            .order_by(Transaction.settlement_date, Transaction.created_at)
        )
        if p.account_id:
            stmt = stmt.where(Transaction.account_id == p.account_id)
        if p.category_id:
            stmt = stmt.where(Transaction.category_id == p.category_id)
        if limit:
            stmt = stmt.limit(limit)
        inflow = outflow = 0
        for t in self.db.scalars(stmt):
            sign = 1 if t.type.value in ("income", "transfer_in") else -1
            amount = sign * t.amount_cents
            inflow += max(amount, 0)
            outflow += max(-amount, 0)
            r.rows.append(
                [
                    t.settlement_date,
                    t.account.name,
                    t.description,
                    TYPE_LABELS[t.type],
                    t.category.name if t.category else "—",
                    STATUS_LABELS[t.status],
                    amount,
                ]
            )
        r.totals = ["Resultado do período", "", "", "", "", "", inflow - outflow]
        r.notes.append(f"Entradas: {format_brl(inflow)} · Saídas: {format_brl(outflow)}.")
        r.notes.append(
            "Valores positivos são entradas e negativos, saídas. Transferências aparecem nas duas contas."
        )
        return r

    def _recorrencias(
        self, user_id: uuid.UUID, p: ReportParams, _limit: int | None
    ) -> ReportResult:
        r = self._base(
            "recorrencias",
            p,
            [
                Column("Descrição"),
                Column("Tipo"),
                Column("Frequência"),
                Column("Conta"),
                Column("Categoria"),
                Column("Status"),
                Column("Valor", "money"),
                Column("Próxima", "date"),
            ],
        )
        service = RecurrenceService(self.db, self.clock, self.audit)
        rules = self.db.scalars(
            select(RecurrenceRule)
            .options(joinedload(RecurrenceRule.account), joinedload(RecurrenceRule.category))
            .where(RecurrenceRule.user_id == user_id)
            .order_by(RecurrenceRule.status, RecurrenceRule.description)
        )
        for rule in rules:
            nxt = service.next_occurrences(rule, 1)
            interval = f" (a cada {rule.interval})" if rule.interval > 1 else ""
            r.rows.append(
                [
                    rule.description,
                    TYPE_LABELS[rule.type],
                    FREQUENCY_LABELS[rule.frequency] + interval,
                    rule.account.name,
                    rule.category.name,
                    RECURRENCE_STATUS_LABELS[rule.status],
                    rule.amount_cents,
                    nxt[0] if nxt else None,
                ]
            )
        return r

    def _cartoes(self, user_id: uuid.UUID, p: ReportParams, _limit: int | None) -> ReportResult:
        r = self._base(
            "cartoes",
            p,
            [
                Column("Cartão"),
                Column("Fatura"),
                Column("Fechamento", "date"),
                Column("Vencimento", "date"),
                Column("Total", "money"),
                Column("Pago", "money"),
                Column("Em aberto", "money"),
                Column("Situação"),
            ],
        )
        labels = {
            "open": "Aberta",
            "closed": "Fechada",
            "partial": "Paga parcialmente",
            "paid": "Paga",
            "overdue": "Vencida",
        }
        today = self.clock.today()
        total = paid = 0
        for s in statement_balances(self.db, user_id):
            month = Month(s.year, s.month)
            if not p.start <= month <= p.end:
                continue
            r.rows.append(
                [
                    s.card_name,
                    month.short_label(),
                    s.closing_date,
                    s.due_date,
                    s.total_cents,
                    s.paid_cents,
                    max(0, s.remaining_cents),
                    labels.get(s.status(today), s.status(today)),
                ]
            )
            total += s.total_cents
            paid += s.paid_cents
        r.totals = ["Total", "", None, None, total, paid, max(0, total - paid), ""]
        r.notes.append(
            "Pagamentos de fatura são saídas de caixa; as compras já foram reconhecidas como despesa nas parcelas (sem dupla contagem)."
        )
        return r


def default_params(today: date) -> ReportParams:
    end = Month.of(today)
    return ReportParams(start=end.shift(-5), end=end)
