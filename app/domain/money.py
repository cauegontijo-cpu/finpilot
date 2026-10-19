"""Conversão e formatação de valores monetários em BRL.

Regra: valores são armazenados e calculados como inteiros em centavos. `Decimal` é
usado somente na fronteira (entrada de texto, saída formatada). Nunca `float`.

Formatos de entrada aceitos (documentados em docs/BUSINESS_RULES.md, RN053):
    1234,56   1.234,56   R$ 1.234,56   -1.234,56   (1.234,56)   1234.56   1234
    1.234,56-  (sinal à direita, comum em extratos)   1.234,56 D / 1.234,56 C
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

CENT = Decimal("0.01")


class MoneyParseError(ValueError):
    """Texto que não representa um valor monetário válido."""


_CLEAN = re.compile(r"[\sR$ ]", re.IGNORECASE)
_BR_FORMAT = re.compile(r"^\d{1,3}(\.\d{3})*(,\d{1,2})?$|^\d+(,\d{1,2})?$")
_DOT_DECIMAL = re.compile(r"^\d+(\.\d{1,2})?$|^\d{1,3}(,\d{3})+\.\d{1,2}$")


def decimal_to_cents(value: Decimal) -> int:
    if not value.is_finite():
        raise MoneyParseError("valor não finito")
    return int((value / CENT).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def cents_to_decimal(cents: int) -> Decimal:
    return (Decimal(cents) * CENT).quantize(CENT)


def parse_brl(text: str) -> int:
    """Converte texto em centavos (com sinal). Lança MoneyParseError se inválido."""
    if text is None:
        raise MoneyParseError("valor vazio")
    raw = _CLEAN.sub("", str(text))
    if not raw:
        raise MoneyParseError("valor vazio")

    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative, raw = True, raw[1:-1]
    upper = raw.upper()
    if upper.endswith("D"):
        negative, raw = True, raw[:-1]
    elif upper.endswith("C"):
        raw = raw[:-1]
    if raw.startswith("-"):
        negative, raw = not negative, raw[1:]
    elif raw.endswith("-"):
        negative, raw = not negative, raw[:-1]
    elif raw.startswith("+"):
        raw = raw[1:]

    if _BR_FORMAT.match(raw):
        normalized = raw.replace(".", "").replace(",", ".")
    elif _DOT_DECIMAL.match(raw):
        normalized = raw.replace(",", "")
    else:
        raise MoneyParseError(f"formato monetário não reconhecido: {text!r}")

    try:
        value = Decimal(normalized)
    except InvalidOperation as exc:  # pragma: no cover - regex já garante formato
        raise MoneyParseError(f"formato monetário não reconhecido: {text!r}") from exc
    cents = decimal_to_cents(value)
    return -cents if negative else cents


def parse_positive_brl(text: str, max_cents: int) -> int:
    cents = parse_brl(text)
    if cents <= 0:
        raise MoneyParseError("o valor deve ser maior que zero")
    if cents > max_cents:
        raise MoneyParseError("valor acima do limite aceito")
    return cents


def format_brl(cents: int, *, symbol: bool = True, signed: bool = False) -> str:
    """Formata centavos em pt-BR: 123456 → 'R$ 1.234,56'."""
    negative = cents < 0
    whole, frac = divmod(abs(cents), 100)
    body = f"{whole:,}".replace(",", ".") + f",{frac:02d}"
    if symbol:
        body = f"R$ {body}"
    if negative:
        return f"-{body}"
    if signed and cents > 0:
        return f"+{body}"
    return body


def format_input_brl(cents: int) -> str:
    """Formato para preencher campos de formulário (sem símbolo)."""
    return format_brl(cents, symbol=False)


def percent(part: int, whole: int) -> Decimal:
    """Percentual com uma casa decimal, determinístico; 0 quando o todo é zero."""
    if whole == 0:
        return Decimal(0)
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), ROUND_HALF_EVEN)
