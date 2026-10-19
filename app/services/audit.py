"""Trilha de auditoria (seção 19)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_sessionmaker
from app.core.security import truncate_ip
from app.models import AuditLog

_FORBIDDEN_KEY = re.compile(r"(password|senha|token|secret|csrf|cookie|hash)", re.IGNORECASE)


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    @property
    def truncated_ip(self) -> str | None:
        return truncate_ip(self.ip)


SYSTEM_META = RequestMeta(request_id="system")


def sanitize(changes: dict[str, Any] | None) -> dict[str, Any] | None:
    if changes is None:
        return None
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if _FORBIDDEN_KEY.search(key):
            continue
        clean[key] = sanitize(value) if isinstance(value, dict) else value
    return clean


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Somente campos alterados, no formato {campo: {"antes": x, "depois": y}}."""
    return {
        key: {"antes": before.get(key), "depois": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }


class AuditService:
    def __init__(self, db: Session, meta: RequestMeta) -> None:
        self.db = db
        self.meta = meta

    def _build(
        self,
        event_type: str,
        *,
        user_id: uuid.UUID | None,
        entity_type: str | None,
        entity_id: uuid.UUID | None,
        outcome: str,
        changes: dict[str, Any] | None,
    ) -> AuditLog:
        return AuditLog(
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            outcome=outcome,
            changes=sanitize(changes),
            request_id=self.meta.request_id,
            ip_address=self.meta.truncated_ip,
            user_agent=(self.meta.user_agent or "")[:200] or None,
        )

    def record(
        self,
        event_type: str,
        *,
        user_id: uuid.UUID | None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        outcome: str = "success",
        changes: dict[str, Any] | None = None,
    ) -> None:
        """Registra na MESMA transação da operação (rollback desfaz o registro)."""
        self.db.add(
            self._build(
                event_type,
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                outcome=outcome,
                changes=changes,
            )
        )

    def record_independent(
        self,
        event_type: str,
        *,
        user_id: uuid.UUID | None,
        outcome: str = "failure",
        changes: dict[str, Any] | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
    ) -> None:
        """Registra em transação separada — para falhas que não devem ser desfeitas."""
        bind = self.db.get_bind()
        maker = get_sessionmaker() if bind is None else None
        with (maker() if maker else Session(bind=bind)) as other:
            other.add(
                self._build(
                    event_type,
                    user_id=user_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    outcome=outcome,
                    changes=changes,
                )
            )
            other.commit()

    def list_for_user(
        self, user_id: uuid.UUID, *, entity_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0
    ) -> list[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.user_id == user_id)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        stmt = stmt.order_by(AuditLog.occurred_at.desc(), AuditLog.id).limit(limit).offset(offset)
        return list(self.db.scalars(stmt))
