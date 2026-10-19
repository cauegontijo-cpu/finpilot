from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.dates import Month
from app.domain.installments import (
    plan_purchase,
    split_installments,
    statement_dates,
    statement_month_for_purchase,
)


def test_split_distributes_residual_cents_to_first_installments() -> None:
    assert split_installments(10_000, 3) == [3334, 3333, 3333]
    assert split_installments(10_001, 3) == [3334, 3334, 3333]
    assert split_installments(999, 1) == [999]
    assert split_installments(5, 5) == [1, 1, 1, 1, 1]


@pytest.mark.parametrize(("total", "count"), [(0, 1), (-100, 2), (100, 0), (100, 49), (3, 4)])
def test_split_rejects_inconsistent_values(total: int, count: int) -> None:
    with pytest.raises(ValueError, match=r"."):
        split_installments(total, count)


@given(st.integers(min_value=48, max_value=10**11), st.integers(min_value=1, max_value=48))
def test_split_sum_always_equals_total_and_is_balanced(total: int, count: int) -> None:
    parts = split_installments(total, count)
    assert sum(parts) == total
    assert len(parts) == count
    assert max(parts) - min(parts) <= 1
    assert parts == sorted(parts, reverse=True)  # determinístico: resíduo nas primeiras


def test_purchase_on_closing_day_goes_to_current_statement() -> None:
    assert statement_month_for_purchase(date(2026, 3, 10), closing_day=10) == Month(2026, 3)


def test_purchase_after_closing_goes_to_next_statement() -> None:
    assert statement_month_for_purchase(date(2026, 3, 11), closing_day=10) == Month(2026, 4)


def test_year_rollover_after_december_closing() -> None:
    assert statement_month_for_purchase(date(2026, 12, 28), closing_day=25) == Month(2027, 1)


def test_closing_day_beyond_month_end_clamps_in_february() -> None:
    # Fechamento dia 30: em fevereiro fecha no dia 28 (ou 29 em ano bissexto).
    assert statement_dates(Month(2027, 2), 30, 7).closing_date == date(2027, 2, 28)
    assert statement_dates(Month(2028, 2), 30, 7).closing_date == date(2028, 2, 29)
    assert statement_month_for_purchase(date(2027, 2, 28), 30) == Month(2027, 2)
    assert statement_month_for_purchase(date(2027, 3, 1), 30) == Month(2027, 3)


def test_due_date_same_month_when_due_after_closing() -> None:
    s = statement_dates(Month(2026, 5), closing_day=3, due_day=10)
    assert s.closing_date == date(2026, 5, 3)
    assert s.due_date == date(2026, 5, 10)


def test_due_date_next_month_when_due_before_closing() -> None:
    s = statement_dates(Month(2026, 12), closing_day=25, due_day=5)
    assert s.due_date == date(2027, 1, 5)


def test_plan_purchase_installments_span_statements_across_year() -> None:
    plan = plan_purchase(date(2026, 11, 20), 100_000, 3, closing_day=15, due_day=25)
    assert [p.statement.month for p in plan] == [Month(2026, 12), Month(2027, 1), Month(2027, 2)]
    assert [p.amount_cents for p in plan] == [33334, 33333, 33333]
    assert [p.number for p in plan] == [1, 2, 3]
    assert sum(p.amount_cents for p in plan) == 100_000
