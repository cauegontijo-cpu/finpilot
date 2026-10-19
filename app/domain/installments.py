"""Regras puras de parcelamento e calendário de faturas (RN061–RN063, RN067).

Política documentada (ADR-007):

* Distribuição de centavos: cada parcela recebe `total // n`; o resto (`total % n`
  centavos) é distribuído um centavo por parcela, a partir da primeira.
  Ex.: R$ 100,00 em 3x → 33,34 + 33,33 + 33,33.
* Fechamento: a fatura do mês M fecha no `closing_day` (limitado ao último dia de M).
  Compras realizadas ATÉ a data de fechamento (inclusive) entram na fatura de M;
  compras posteriores entram na fatura de M+1.
* A parcela k (1-based) pertence à fatura (fatura da compra) + (k-1) meses.
* Vencimento: se `due_day > closing_day`, vence no mesmo mês do fechamento;
  caso contrário, no mês seguinte. Dias inexistentes → último dia do mês.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.dates import Month, clamp_day

MAX_INSTALLMENTS = 48


def split_installments(total_cents: int, count: int) -> list[int]:
    if total_cents <= 0:
        raise ValueError("o valor da compra deve ser positivo")
    if not 1 <= count <= MAX_INSTALLMENTS:
        raise ValueError(f"número de parcelas deve estar entre 1 e {MAX_INSTALLMENTS}")
    if total_cents < count:
        raise ValueError("valor insuficiente para o número de parcelas (mínimo de R$ 0,01 cada)")
    base, remainder = divmod(total_cents, count)
    return [base + (1 if i < remainder else 0) for i in range(count)]


@dataclass(frozen=True)
class StatementDates:
    month: Month
    closing_date: date
    due_date: date


def statement_dates(month: Month, closing_day: int, due_day: int) -> StatementDates:
    closing = clamp_day(month.year, month.month, closing_day)
    due_month = month if due_day > closing_day else month.shift(1)
    due = clamp_day(due_month.year, due_month.month, due_day)
    return StatementDates(month=month, closing_date=closing, due_date=due)


def statement_month_for_purchase(purchase_date: date, closing_day: int) -> Month:
    month = Month.of(purchase_date)
    closing = clamp_day(month.year, month.month, closing_day)
    return month if purchase_date <= closing else month.shift(1)


@dataclass(frozen=True)
class InstallmentPlan:
    number: int
    amount_cents: int
    statement: StatementDates


def plan_purchase(
    purchase_date: date, total_cents: int, count: int, closing_day: int, due_day: int
) -> list[InstallmentPlan]:
    amounts = split_installments(total_cents, count)
    first = statement_month_for_purchase(purchase_date, closing_day)
    return [
        InstallmentPlan(
            number=i + 1,
            amount_cents=amount,
            statement=statement_dates(first.shift(i), closing_day, due_day),
        )
        for i, amount in enumerate(amounts)
    ]
