from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.dates import Month, local_date, month_range
from app.domain.recurrence import Frequency, nth_occurrence, occurrences_between


def test_monthly_anchor_31_uses_last_valid_day_and_returns_to_31() -> None:
    dates = list(
        occurrences_between(
            date(2026, 1, 31), Frequency.MONTHLY, 1, date(2026, 1, 1), date(2026, 6, 30)
        )
    )
    assert dates == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_leap_year_february() -> None:
    assert nth_occurrence(date(2028, 1, 30), Frequency.MONTHLY, 1, 1) == date(2028, 2, 29)


def test_yearly_feb_29_falls_back_to_feb_28() -> None:
    assert nth_occurrence(date(2028, 2, 29), Frequency.YEARLY, 1, 1) == date(2029, 2, 28)
    assert nth_occurrence(date(2028, 2, 29), Frequency.YEARLY, 1, 4) == date(2032, 2, 29)


def test_daily_weekly_and_interval() -> None:
    assert nth_occurrence(date(2026, 1, 1), Frequency.DAILY, 1, 31) == date(2026, 2, 1)
    assert nth_occurrence(date(2026, 1, 1), Frequency.WEEKLY, 2, 2) == date(2026, 1, 29)
    assert nth_occurrence(date(2026, 1, 15), Frequency.MONTHLY, 3, 1) == date(2026, 4, 15)


def test_window_and_end_date_are_respected() -> None:
    dates = list(
        occurrences_between(
            date(2026, 1, 10),
            Frequency.MONTHLY,
            1,
            window_start=date(2026, 3, 1),
            window_end=date(2026, 12, 31),
            end_date=date(2026, 5, 10),
        )
    )
    assert dates == [date(2026, 3, 10), date(2026, 4, 10), date(2026, 5, 10)]


def test_window_before_start_yields_nothing() -> None:
    assert not list(
        occurrences_between(
            date(2026, 5, 1), Frequency.DAILY, 1, date(2026, 1, 1), date(2026, 4, 30)
        )
    )


@given(
    st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 12, 31)),
    st.sampled_from(list(Frequency)),
    st.integers(min_value=1, max_value=6),
    st.integers(min_value=0, max_value=400),
    st.integers(min_value=0, max_value=200),
)
def test_window_iteration_matches_index_formula(
    start: date, freq: Frequency, interval: int, offset: int, length: int
) -> None:
    """Propriedade: a janela nunca pula nem duplica ocorrências."""
    window_start = start + timedelta(days=offset)
    window_end = window_start + timedelta(days=length)
    got = list(occurrences_between(start, freq, interval, window_start, window_end))
    expected = []
    n = 0
    while (d := nth_occurrence(start, freq, interval, n)) <= window_end:
        if d >= window_start:
            expected.append(d)
        n += 1
    assert got == expected
    assert len(set(got)) == len(got)


def test_local_date_uses_sao_paulo_timezone() -> None:
    # 01/03/2026 01:30 UTC ainda é 28/02/2026 em São Paulo (UTC-3).
    moment = datetime(2026, 3, 1, 1, 30, tzinfo=UTC)
    assert local_date(moment) == date(2026, 2, 28)
    with pytest.raises(ValueError, match="fuso"):
        local_date(datetime(2026, 3, 1, 1, 30))


def test_month_helpers() -> None:
    m = Month(2026, 12)
    assert m.shift(1) == Month(2027, 1)
    assert m.shift(-12) == Month(2025, 12)
    assert m.last_day == date(2026, 12, 31)
    assert Month(2026, 2).last_day == date(2026, 2, 28)
    assert Month.parse("2026-07") == Month(2026, 7)
    assert len(month_range(Month(2025, 11), Month(2026, 10))) == 12
    with pytest.raises(ValueError, match="mês"):
        Month(2026, 13)
