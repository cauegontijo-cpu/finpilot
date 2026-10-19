from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.money import (
    MoneyParseError,
    cents_to_decimal,
    format_brl,
    parse_brl,
    parse_positive_brl,
    percent,
)


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("1234,56", 123456),
        ("1.234,56", 123456),
        ("R$ 1.234,56", 123456),
        ("R$ 1.234,56", 123456),
        ("-1.234,56", -123456),
        ("(1.234,56)", -123456),
        ("1.234,56-", -123456),
        ("1.234,56 D", -123456),
        ("1.234,56 C", 123456),
        ("1234.56", 123456),
        ("1,234.56", 123456),
        ("1234", 123400),
        ("0,5", 50),
        ("+10,00", 1000),
        ("1.000.000,00", 100000000),
    ],
)
def test_parse_brl_formats(text: str, cents: int) -> None:
    assert parse_brl(text) == cents


@pytest.mark.parametrize(
    "text", ["", "   ", "abc", "1,2,3", "12,345", "1.23.4", "R$", "--5", "1e5"]
)
def test_parse_brl_rejects_invalid(text: str) -> None:
    with pytest.raises(MoneyParseError):
        parse_brl(text)


def test_parse_positive_rejects_zero_negative_and_limit() -> None:
    with pytest.raises(MoneyParseError):
        parse_positive_brl("0,00", 10_000)
    with pytest.raises(MoneyParseError):
        parse_positive_brl("-1,00", 10_000)
    with pytest.raises(MoneyParseError):
        parse_positive_brl("100,01", 10_000)
    assert parse_positive_brl("100,00", 10_000) == 10_000


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "R$ 0,00"),
        (5, "R$ 0,05"),
        (123456, "R$ 1.234,56"),
        (-123456, "-R$ 1.234,56"),
        (100000000, "R$ 1.000.000,00"),
    ],
)
def test_format_brl(cents: int, expected: str) -> None:
    assert format_brl(cents) == expected


def test_format_brl_signed_and_without_symbol() -> None:
    assert format_brl(1000, signed=True) == "+R$ 10,00"
    assert format_brl(1000, symbol=False) == "10,00"


@given(st.integers(min_value=-(10**12), max_value=10**12))
def test_format_parse_roundtrip(cents: int) -> None:
    assert parse_brl(format_brl(cents)) == cents


def test_cents_to_decimal_is_exact() -> None:
    assert cents_to_decimal(1) == Decimal("0.01")
    assert cents_to_decimal(-123456) == Decimal("-1234.56")


def test_percent_deterministic() -> None:
    assert percent(1, 3) == Decimal("33.3")
    assert percent(2, 3) == Decimal("66.7")
    assert percent(5, 0) == Decimal(0)
    assert percent(120_000, 100_000) == Decimal("120.0")
