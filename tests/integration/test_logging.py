"""Logging estruturado: campos extras válidos, redação de segredos e request id."""

import json
import logging
from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.logging import JsonFormatter, redact
from app.domain.ledger import TransactionType
from app.domain.recurrence import Frequency
from app.jobs import maintenance
from app.services.recurrences import RecurrenceInput, RecurrenceService
from tests.factories import audit, category, make_account


def test_redaction_of_sensitive_keys() -> None:
    data = {"password": "x", "nested": {"session_token": "y", "ok": 1}, "Authorization": "Bearer z", "amount": 10}
    assert redact(data) == {"password": "[REDACTED]", "nested": {"session_token": "[REDACTED]", "ok": 1}, "Authorization": "[REDACTED]", "amount": 10}


def test_json_formatter_outputs_valid_json_with_redaction() -> None:
    record = logging.LogRecord("finpilot", logging.INFO, __file__, 1, "evento", (), None)
    record.csrf_token = "segredo"
    record.path = "/app"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "evento" and payload["path"] == "/app" and payload["csrf_token"] == "[REDACTED]"


def test_info_logs_of_jobs_do_not_collide_with_logrecord_attributes(db: Session, user: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Regressão: `extra={"created": ...}` derrubava o agendador (atributo reservado do LogRecord)."""
    caplog.set_level(logging.INFO)
    account = make_account(db, user, initial=100_000)
    RecurrenceService(db, Clock(), audit(db)).create(
        user.id,
        RecurrenceInput(TransactionType.EXPENSE, account.id, category(db, user, "Moradia").id, "Aluguel", 1_000, Frequency.MONTHLY, date(2026, 11, 1)),
    )
    assert any(r.getMessage() == "ocorrências geradas" for r in caplog.records)


def test_maintenance_job_runs(migrated_engine: Any, caplog: pytest.LogCaptureFixture) -> None:
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    caplog.set_level(logging.INFO)
    counts = maintenance.run_once(sessionmaker(bind=migrated_engine))
    assert set(counts) >= {"sessoes", "codigos_email", "chaves_idempotencia", "lotes_importacao", "auditoria"}
