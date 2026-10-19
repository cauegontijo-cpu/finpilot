"""Lotes de importação de extratos CSV (RN051–RN060).

O arquivo enviado nunca é gravado no filesystem: as linhas são parseadas em memória
e armazenadas aqui, isoladas por usuário.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, str_enum, user_fk, uuid_pk


class ImportStatus(StrEnum):
    UPLOADED = "uploaded"
    MAPPED = "mapped"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class ImportRowStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    IMPORTED = "imported"
    FAILED = "failed"
    SKIPPED = "skipped"


class ImportBatch(TimestampMixin, Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "user_id"], ["accounts.id", "accounts.user_id"], name="fk_import_batches_account"
        ),
        Index("ix_import_batches_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = user_fk()
    account_id: Mapped[uuid.UUID]
    status: Mapped[ImportStatus] = mapped_column(str_enum(ImportStatus, "import_status"))
    display_name: Mapped[str] = mapped_column(String(120))
    file_sha256: Mapped[str] = mapped_column(String(64))
    encoding: Mapped[str] = mapped_column(String(20))
    delimiter: Mapped[str] = mapped_column(String(1))
    has_header: Mapped[bool]
    headers: Mapped[list[str]] = mapped_column(JSONB)
    column_count: Mapped[int] = mapped_column(Integer)
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime | None]
    expires_at: Mapped[datetime]

    rows: Mapped[list[ImportRow]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ImportRow.row_number",
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        Index("uq_import_rows_batch_number", "batch_id", "row_number", unique=True),
        CheckConstraint("amount_cents IS NULL OR amount_cents <> 0", name="amount_nonzero"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[ImportRowStatus | None] = mapped_column(str_enum(ImportRowStatus, "import_row_status"))
    occurred_on: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(140))
    amount_cents: Mapped[int | None]
    category_name: Mapped[str | None] = mapped_column(String(50))
    external_id: Mapped[str | None] = mapped_column(String(120))
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    errors: Mapped[list[str] | None] = mapped_column(JSONB)
    duplicate_of_id: Mapped[uuid.UUID | None]
    selected: Mapped[bool] = mapped_column(server_default="false")
    transaction_id: Mapped[uuid.UUID | None]

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")
