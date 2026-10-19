"""Limpeza periódica: sessões, tokens, chaves de idempotência e lotes de importação expirados;
retenção da auditoria (executada com flag de manutenção — ver migration da auditoria).

    python -m app.jobs.maintenance
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.logging import configure_logging
from app.models import AuditLog, EmailToken, IdempotencyKey, ImportBatch, ImportStatus, PasswordResetToken, UserSession

logger = logging.getLogger("finpilot.jobs.maintenance")


def run_once(maker: sessionmaker[Session] | None = None, clock: Clock | None = None) -> dict[str, int]:
    maker = maker or get_sessionmaker()
    clock = clock or Clock()
    settings = get_settings()
    now = clock.now()
    counts: dict[str, int] = {}
    with maker() as db:
        counts["sessions"] = db.execute(
            delete(UserSession).where(or_(UserSession.expires_at < now, UserSession.revoked_at < now - timedelta(days=30)))
        ).rowcount
        counts["email_tokens"] = db.execute(delete(EmailToken).where(EmailToken.expires_at < now - timedelta(days=7))).rowcount
        counts["reset_tokens"] = db.execute(
            delete(PasswordResetToken).where(PasswordResetToken.expires_at < now - timedelta(days=7))
        ).rowcount
        counts["idempotency_keys"] = db.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < now)).rowcount
        counts["import_batches"] = db.execute(
            delete(ImportBatch).where(ImportBatch.expires_at < now, ImportBatch.status != ImportStatus.CONFIRMED)
        ).rowcount
        db.commit()

    with maker() as db:
        try:
            # Exige papel com DELETE na tabela (migrator/manutenção); o papel da aplicação não tem.
            db.execute(text("SET LOCAL finpilot.audit_maintenance = 'on'"))
            counts["audit_logs"] = db.execute(
                delete(AuditLog).where(
                    or_(
                        AuditLog.occurred_at < now - timedelta(days=settings.audit_retention_days),
                        (AuditLog.event_type == "auth.login_failed")
                        & (AuditLog.occurred_at < now - timedelta(days=settings.audit_auth_failure_retention_days)),
                    )
                )
            ).rowcount
            db.commit()
        except SQLAlchemyError:  # sem privilégio de manutenção: registra e segue
            db.rollback()
            logger.warning("retenção da auditoria não executada (papel sem privilégio de manutenção)")
            counts["audit_logs"] = -1
    logger.info("manutenção concluída", extra=counts)
    return {k: int(v) for k, v in counts.items()}


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    run_once()
