"""Acesso a dados sempre escopado pelo usuário autenticado (RN002, RN020, seção 18.1).

Toda leitura de recurso de usuário passa por `get_owned`: um id de outro usuário é
tratado exatamente como um id inexistente (404), sem revelar existência.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.errors import NotFoundError, ValidationFailed


class Owned(Protocol):
    id: Any
    user_id: Any


T = TypeVar("T")


def parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def get_owned(
    db: Session,
    model: type[T],
    obj_id: str | uuid.UUID | None,
    user_id: uuid.UUID,
    *,
    for_update: bool = False,
    message: str = "Recurso não encontrado.",
) -> T:
    parsed = parse_uuid(obj_id)
    if parsed is None:
        raise NotFoundError(message)
    stmt = select(model).where(model.id == parsed, model.user_id == user_id)  # type: ignore[attr-defined]
    if for_update:
        stmt = stmt.with_for_update()
    obj = db.scalars(stmt).one_or_none()
    if obj is None:
        raise NotFoundError(message)
    return obj


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1


MAX_PAGE_SIZE = 100


def paginate(db: Session, stmt: Select[Any], page: int, page_size: int) -> Page[Any]:
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = list(db.scalars(stmt.limit(page_size).offset((page - 1) * page_size)))
    return Page(items=items, total=total, page=page, page_size=page_size)


def apply_sort(
    stmt: Select[Any],
    sort: str | None,
    allowed: dict[str, InstrumentedAttribute[Any]],
    default: str,
    tiebreaker: InstrumentedAttribute[Any],
) -> Select[Any]:
    """Ordenação apenas por campos permitidos (`-campo` = decrescente)."""
    key = sort or default
    descending = key.startswith("-")
    name = key.lstrip("-")
    column = allowed.get(name)
    if column is None:
        raise ValidationFailed(
            f"Ordenação inválida. Campos permitidos: {', '.join(sorted(allowed))}.",
            code="invalid_sort",
        )
    order = column.desc() if descending else column.asc()
    return stmt.order_by(order, tiebreaker.desc() if descending else tiebreaker.asc())
