"""Conversão de campos de formulários HTML (texto pt-BR) para tipos do domínio.

As regras de negócio continuam nos serviços; aqui apenas interpretamos a entrada
e acumulamos erros por campo para reexibir o formulário.
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import TypeVar

from starlette.datastructures import FormData

from app.core.errors import ValidationFailed
from app.domain.dates import Month
from app.domain.money import MoneyParseError, parse_brl, parse_positive_brl
from app.models.finance import MAX_CENTS
from app.repositories.base import parse_uuid
from app.utils.validation import parse_date

E = TypeVar("E", bound=StrEnum)


class FormReader:
    def __init__(self, form: FormData) -> None:
        self.form = form
        self.errors: dict[str, str] = {}

    def raw(self, name: str) -> str:
        value = self.form.get(name)
        return value.strip() if isinstance(value, str) else ""

    def text(self, name: str, default: str = "") -> str:
        return self.raw(name) or default

    def bool(self, name: str) -> bool:
        return self.raw(name).lower() in {"true", "on", "1", "sim"}

    def money(self, name: str, *, positive: bool = True) -> int:
        try:
            if positive:
                return parse_positive_brl(self.raw(name), MAX_CENTS)
            cents = parse_brl(self.raw(name) or "0")
            if abs(cents) > MAX_CENTS:
                raise MoneyParseError("valor acima do limite aceito")
            return cents
        except MoneyParseError as exc:
            self.errors[name] = _money_message(str(exc))
            return 0

    def date(self, name: str, *, required: bool = True) -> date | None:
        try:
            return parse_date(self.raw(name), name, required=required)
        except ValidationFailed as exc:
            self.errors[name] = exc.field_errors.get(name, "Data inválida")
            return None

    def required_date(self, name: str, fallback: date) -> date:
        return self.date(name) or fallback

    def enum(self, name: str, enum_cls: type[E], default: E | None = None) -> E:
        value = self.raw(name)
        try:
            return enum_cls(value)
        except ValueError:
            if default is not None and not value:
                return default
            self.errors[name] = "Opção inválida"
            return default or next(iter(enum_cls))

    def uuid(self, name: str) -> uuid.UUID | None:
        return parse_uuid(self.raw(name))

    def integer(self, name: str, default: int, *, min_value: int, max_value: int) -> int:
        value = self.raw(name)
        if not value:
            return default
        try:
            number = int(value)
        except ValueError:
            self.errors[name] = "Número inválido"
            return default
        if not min_value <= number <= max_value:
            self.errors[name] = f"Use um valor entre {min_value} e {max_value}"
        return number

    def raise_if_errors(self) -> None:
        if self.errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=dict(self.errors))


def _money_message(reason: str) -> str:
    if "vazio" in reason:
        return "Informe o valor."
    if "maior que zero" in reason:
        return "O valor deve ser maior que zero."
    if "limite" in reason:
        return "Valor acima do limite aceito."
    return "Valor inválido. Use o formato 1.234,56."


def month_param(value: str | None, today: date) -> Month:
    if value:
        try:
            return Month.parse(value)
        except (ValueError, TypeError):
            pass
    return Month.of(today)


def merge_errors(*sources: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for src in sources:
        merged.update(src)
    # Os serviços usam "amount"; os formulários, "amount" também — mantém chaves estáveis.
    return merged
