"""Idempotência de operações financeiras (RN017, RN028, RN055).

A chave é registrada na MESMA transação da operação. Requisições concorrentes com a
mesma chave serializam no índice único `(user_id, key)`: a segunda aguarda o commit da
primeira e então recebe o recurso já criado (replay), sem reaplicar a operação.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ConflictError, IdempotencyConflict, ValidationFailed
from app.models import IdempotencyKey

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
KEY_TTL = timedelta(days=7)


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyGuard:
    def __init__(self, db: Session, clock: Clock, user_id: uuid.UUID, key: str, scope: str) -> None:
        if not KEY_PATTERN.match(key or ""):
            raise ValidationFailed(
                "Chave de idempotência ausente ou inválida.", code="idempotency_key_invalid"
            )
        self.db = db
        self.clock = clock
        self.user_id = user_id
        self.key = key
        self.scope = scope
        self._row_id: uuid.UUID | None = None

    def begin(self, payload: dict[str, Any]) -> uuid.UUID | None:
        """Retorna o id do recurso já criado (replay) ou None se a operação deve prosseguir."""
        fingerprint = request_fingerprint(payload)
        now = self.clock.now()
        stmt = (
            insert(IdempotencyKey)
            .values(
                id=uuid.uuid4(),
                user_id=self.user_id,
                key=self.key,
                scope=self.scope,
                request_hash=fingerprint,
                created_at=now,
                expires_at=now + KEY_TTL,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(IdempotencyKey.id)
        )
        inserted = self.db.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            self._row_id = inserted
            return None

        existing = self.db.scalars(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == self.user_id, IdempotencyKey.key == self.key
            )
        ).one()
        if existing.scope != self.scope or existing.request_hash != fingerprint:
            raise IdempotencyConflict
        if existing.resource_id is None:
            raise ConflictError("Operação ainda em processamento.", code="idempotency_in_progress")
        return existing.resource_id

    def complete(self, resource_id: uuid.UUID) -> None:
        if self._row_id is None:
            raise RuntimeError("complete() chamado sem begin() bem-sucedido")
        row = self.db.get(IdempotencyKey, self._row_id)
        assert row is not None
        row.resource_id = resource_id
