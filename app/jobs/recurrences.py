"""Job de geração de recorrências — executável por agendador (cron, compose `scheduler`).

    python -m app.jobs.recurrences

Garantias (RN048/RN050):
* `pg_try_advisory_lock` impede duas execuções simultâneas do job;
* cada regra é processada em sua própria transação, com `FOR UPDATE SKIP LOCKED`;
* o índice único (recurrence_id, occurrence_date) impede duplicatas mesmo sem o lock;
* a partir de `generated_until`, ocorrências pendentes de execuções que falharam são
  recuperadas na próxima execução.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock
from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.core.logging import configure_logging
from app.models import RecurrenceRule, RecurrenceStatus
from app.services.audit import SYSTEM_META, AuditService
from app.services.recurrences import RecurrenceService

logger = logging.getLogger("finpilot.jobs.recurrences")
ADVISORY_LOCK_KEY = 0x46_50_52_43  # "FPRC"


@dataclass
class JobReport:
    acquired: bool = True
    rules: int = 0
    created: int = 0
    settled: int = 0
    failures: list[str] = field(default_factory=list)


def run_once(maker: sessionmaker[Session] | None = None, clock: Clock | None = None) -> JobReport:
    maker = maker or get_sessionmaker()
    clock = clock or Clock()
    horizon_days = get_settings().recurrence_horizon_days
    report = JobReport()
    with maker() as lock_session:
        acquired = lock_session.scalar(select(text(f"pg_try_advisory_lock({ADVISORY_LOCK_KEY})")))
        if not acquired:
            logger.info("outra execução do job está em andamento; encerrando")
            report.acquired = False
            return report
        try:
            until = clock.today() + timedelta(days=horizon_days)
            with maker() as db:
                rule_ids = list(
                    db.scalars(
                        select(RecurrenceRule.id).where(
                            RecurrenceRule.status == RecurrenceStatus.ACTIVE,
                            or_(RecurrenceRule.generated_until.is_(None), RecurrenceRule.generated_until < until),
                        )
                    )
                )
            for rule_id in rule_ids:
                with maker() as db:
                    try:
                        rule = db.scalars(
                            select(RecurrenceRule).where(RecurrenceRule.id == rule_id).with_for_update(skip_locked=True)
                        ).one_or_none()
                        if rule is None:
                            continue
                        service = RecurrenceService(db, clock, AuditService(db, SYSTEM_META), horizon_days)
                        result = service.generate(rule, until)
                        db.commit()
                        report.rules += 1
                        report.created += result.created
                        report.settled += result.settled
                    except SQLAlchemyError:
                        db.rollback()
                        logger.exception("falha ao gerar ocorrências", extra={"recurrence_id": str(rule_id)})
                        report.failures.append(str(rule_id))
        finally:
            lock_session.execute(text(f"SELECT pg_advisory_unlock({ADVISORY_LOCK_KEY})"))
            lock_session.commit()
    logger.info(
        "job de recorrências concluído",
        extra={"rules": report.rules, "created": report.created, "settled": report.settled, "failures": len(report.failures)},
    )
    return report


def main() -> None:
    configure_logging(get_settings().log_level)
    report = run_once()
    sys.exit(1 if report.failures else 0)


if __name__ == "__main__":
    main()
