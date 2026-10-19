"""Schemas base da API: entrada estrita (sem mass assignment) e saída sem campos internos."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domain.money import cents_to_decimal
from app.models.finance import MAX_CENTS

T = TypeVar("T")

PositiveCents = Annotated[
    int, Field(gt=0, le=MAX_CENTS, description="Valor em centavos (ex.: 12345 = R$ 123,45)")
]
SignedCents = Annotated[
    int, Field(ge=-MAX_CENTS, le=MAX_CENTS, description="Valor em centavos, com sinal")
]


class InputModel(BaseModel):
    """Campos não declarados são rejeitados (422) — impede mass assignment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OutputModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MoneyMixin(OutputModel):
    amount_cents: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount(self) -> Decimal:
        """Valor decimal em reais, serializado como string exata."""
        return cents_to_decimal(self.amount_cents)


class PageOut(BaseModel, Generic[T]):  # noqa: UP046 - forma suportada pelo Pydantic
    items: list[T]
    total: int
    page: int
    page_size: int


class Problem(BaseModel):
    """Erro padronizado (RFC 9457 — application/problem+json)."""

    type: str
    title: str
    status: int
    detail: str
    code: str | None = None
    request_id: str | None = None
    errors: list[dict[str, str]] | None = None


PROBLEM_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": Problem, "description": "Não autenticado"},
    403: {"model": Problem, "description": "CSRF inválido ou e-mail não verificado"},
    404: {"model": Problem, "description": "Recurso inexistente ou de outro usuário"},
    409: {"model": Problem, "description": "Conflito (duplicidade / idempotência)"},
    422: {"model": Problem, "description": "Dados inválidos ou regra de negócio violada"},
    429: {"model": Problem, "description": "Limite de requisições excedido"},
}
