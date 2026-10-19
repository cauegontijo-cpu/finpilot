"""Exportação dos dados pessoais e financeiros do titular (LGPD — portabilidade)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AuditLog,
    Budget,
    CardInstallment,
    CardPayment,
    CardPurchase,
    CardStatement,
    Category,
    CreditCard,
    Goal,
    GoalAllocation,
    RecurrenceRule,
    Transaction,
    Transfer,
    User,
    UserSession,
)

_EXCLUDED_COLUMNS = {"password_hash", "token_hash", "user_id"}
_EXPORT_MODELS: list[tuple[str, type[Any]]] = [
    ("contas", Account),
    ("categorias", Category),
    ("transacoes", Transaction),
    ("transferencias", Transfer),
    ("orcamentos", Budget),
    ("metas", Goal),
    ("alocacoes_metas", GoalAllocation),
    ("recorrencias", RecurrenceRule),
    ("cartoes", CreditCard),
    ("faturas", CardStatement),
    ("compras_cartao", CardPurchase),
    ("parcelas_cartao", CardInstallment),
    ("pagamentos_fatura", CardPayment),
    ("sessoes", UserSession),
    ("auditoria", AuditLog),
]


def _serialize(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return value


def _row(obj: Any) -> dict[str, Any]:
    mapper = inspect(type(obj))
    return {
        col.key: _serialize(getattr(obj, col.key))
        for col in mapper.column_attrs
        if col.key not in _EXCLUDED_COLUMNS
    }


def export_user_data(db: Session, user: User, generated_at: datetime) -> dict[str, Any]:
    data: dict[str, Any] = {
        "formato": "finpilot-export/1",
        "gerado_em": generated_at.isoformat(),
        "observacao": "Valores monetários em centavos (campos *_cents). Datas de eventos em UTC.",
        "usuario": _row(user),
    }
    for key, model in _EXPORT_MODELS:
        rows = db.scalars(select(model).where(model.user_id == user.id)).all()
        data[key] = [_row(r) for r in rows]
    return data
