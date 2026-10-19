"""Cálculo determinístico de ocorrências de recorrências (RN046).

Política (ADR-008): a n-ésima ocorrência é calculada a partir da data inicial
(âncora), nunca a partir da ocorrência anterior. Para frequências mensal e anual,
se o dia da âncora não existir no mês de destino, usa-se o último dia válido do mês.
Ex.: âncora 31/01 mensal → 31/01, 28/02 (29/02 em bissexto), 31/03, 30/04, 31/05…
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from enum import StrEnum

from app.domain.dates import add_months, clamp_day


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


FREQUENCY_LABELS = {
    Frequency.DAILY: "Diária",
    Frequency.WEEKLY: "Semanal",
    Frequency.MONTHLY: "Mensal",
    Frequency.YEARLY: "Anual",
}

MAX_ITERATIONS = 100_000


def nth_occurrence(start: date, frequency: Frequency, interval: int, n: int) -> date:
    if interval < 1:
        raise ValueError("intervalo deve ser >= 1")
    match frequency:
        case Frequency.DAILY:
            return start + timedelta(days=n * interval)
        case Frequency.WEEKLY:
            return start + timedelta(weeks=n * interval)
        case Frequency.MONTHLY:
            year, month = add_months(start.year, start.month, n * interval)
            return clamp_day(year, month, start.day)
        case Frequency.YEARLY:
            year, month = add_months(start.year, start.month, 12 * n * interval)
            return clamp_day(year, month, start.day)


def occurrences_between(
    start: date,
    frequency: Frequency,
    interval: int,
    window_start: date,
    window_end: date,
    end_date: date | None = None,
) -> Iterator[date]:
    """Ocorrências com `window_start <= d <= window_end`, respeitando `end_date`."""
    limit = min(window_end, end_date) if end_date else window_end
    if limit < start:
        return
    n = _first_index_at_or_after(start, frequency, interval, window_start)
    for _ in range(MAX_ITERATIONS):
        current = nth_occurrence(start, frequency, interval, n)
        if current > limit:
            return
        if current >= window_start:
            yield current
        n += 1


def _first_index_at_or_after(start: date, frequency: Frequency, interval: int, target: date) -> int:
    if target <= start:
        return 0
    days = (target - start).days
    match frequency:
        case Frequency.DAILY:
            estimate = days // interval
        case Frequency.WEEKLY:
            estimate = days // (7 * interval)
        case Frequency.MONTHLY:
            months = (target.year - start.year) * 12 + target.month - start.month
            estimate = months // interval
        case Frequency.YEARLY:
            estimate = (target.year - start.year) // interval
    # Recuo de segurança para nunca pular ocorrências por arredondamento.
    return max(0, estimate - 1)
