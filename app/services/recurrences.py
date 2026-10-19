"""Transações recorrentes (seção 12, RN044–RN050).

Geração idempotente: cada ocorrência é um lançamento com `recurrence_id` +
`occurrence_date`, protegido por índice único — reexecuções, falhas e execuções
simultâneas do agendador nunca duplicam ocorrências (RN045/RN048/RN050).
"""

from __future__ import annotations

import builtins
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import islice

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, InsufficientFunds, ValidationFailed, expect
from app.domain.ledger import TransactionOrigin, TransactionStatus, TransactionType
from app.domain.recurrence import Frequency, occurrences_between
from app.models import CategoryKind, RecurrenceRule, RecurrenceStatus, Transaction
from app.models.base import model_snapshot
from app.models.finance import MAX_CENTS
from app.repositories.base import get_owned
from app.services.accounts import AccountService
from app.services.audit import AuditService, diff
from app.services.categories import CategoryService
from app.services.ledger import Ledger
from app.utils.validation import clean_multiline, clean_text

logger = logging.getLogger(__name__)

#: Ao criar uma regra com início no passado, gera no máximo este período retroativo.
INITIAL_BACKFILL_DAYS = 60
_FIELDS = [
    "type",
    "description",
    "amount_cents",
    "account_id",
    "category_id",
    "frequency",
    "interval",
    "start_date",
    "end_date",
    "status",
    "auto_settle",
    "notes",
]
_SCHEDULE_FIELDS = {"frequency", "interval", "start_date", "end_date"}


@dataclass(frozen=True)
class RecurrenceInput:
    type: TransactionType
    account_id: str | uuid.UUID
    category_id: str | uuid.UUID
    description: str
    amount_cents: int
    frequency: Frequency
    start_date: date
    interval: int = 1
    end_date: date | None = None
    auto_settle: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class GenerationResult:
    created: int
    settled: int


class RecurrenceService:
    def __init__(
        self, db: Session, clock: Clock, audit: AuditService, horizon_days: int = 90
    ) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.horizon_days = horizon_days
        self.accounts = AccountService(db, clock, audit)
        self.categories = CategoryService(db, clock, audit)

    # ---------------------------------------------------------------- consultas
    def get(
        self, user_id: uuid.UUID, rule_id: str | uuid.UUID, *, for_update: bool = False
    ) -> RecurrenceRule:
        return get_owned(
            self.db,
            RecurrenceRule,
            rule_id,
            user_id,
            for_update=for_update,
            message="Recorrência não encontrada.",
        )

    def list(self, user_id: uuid.UUID) -> builtins.list[RecurrenceRule]:
        return list(
            self.db.scalars(
                select(RecurrenceRule)
                .options(joinedload(RecurrenceRule.account), joinedload(RecurrenceRule.category))
                .where(RecurrenceRule.user_id == user_id)
                .order_by(RecurrenceRule.status, RecurrenceRule.description)
            )
        )

    def next_occurrences(self, rule: RecurrenceRule, count: int = 5) -> builtins.list[date]:
        if rule.status is not RecurrenceStatus.ACTIVE:
            return []
        today = self.clock.today()
        dates = occurrences_between(
            rule.start_date,
            rule.frequency,
            rule.interval,
            today,
            today + timedelta(days=3660),
            rule.end_date,
        )
        return list(islice(dates, count))

    def occurrences(
        self, user_id: uuid.UUID, rule_id: str, limit: int = 24
    ) -> builtins.list[Transaction]:
        rule = self.get(user_id, rule_id)
        return list(
            self.db.scalars(
                select(Transaction)
                .where(Transaction.recurrence_id == rule.id, Transaction.user_id == user_id)
                .order_by(Transaction.occurrence_date.desc())
                .limit(limit)
            )
        )

    # ---------------------------------------------------------------- validação
    def _validate(self, user_id: uuid.UUID, data: RecurrenceInput) -> RecurrenceInput:
        errors: dict[str, str] = {}
        if data.type not in (TransactionType.INCOME, TransactionType.EXPENSE):
            errors["type"] = "Recorrências podem ser receitas ou despesas."
        description = clean_text(data.description, max_len=140)
        if not description:
            errors["description"] = "Informe a descrição."
        if data.amount_cents <= 0 or data.amount_cents > MAX_CENTS:
            errors["amount"] = "O valor deve ser maior que zero."
        if not 1 <= data.interval <= 365:
            errors["interval"] = "Intervalo entre 1 e 365."
        if data.end_date and data.end_date < data.start_date:
            errors["end_date"] = "A data final deve ser posterior à inicial."
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)
        account = self.accounts.require_active(user_id, data.account_id)
        kind = CategoryKind.INCOME if data.type is TransactionType.INCOME else CategoryKind.EXPENSE
        category = self.categories.require_for_type(user_id, data.category_id, kind)
        return RecurrenceInput(
            type=data.type,
            account_id=account.id,
            category_id=category.id,
            description=description,
            amount_cents=data.amount_cents,
            frequency=data.frequency,
            start_date=data.start_date,
            interval=data.interval,
            end_date=data.end_date,
            auto_settle=data.auto_settle,
            notes=clean_multiline(data.notes, max_len=1000),
        )

    # ---------------------------------------------------------------- escrita
    def create(self, user_id: uuid.UUID, data: RecurrenceInput) -> RecurrenceRule:
        data = self._validate(user_id, data)
        today = self.clock.today()
        rule = RecurrenceRule(user_id=user_id, status=RecurrenceStatus.ACTIVE, **data.__dict__)
        # Backfill limitado: não gera anos de ocorrências passadas ao criar a regra.
        rule.generated_until = max(
            rule.start_date, today - timedelta(days=INITIAL_BACKFILL_DAYS)
        ) - timedelta(days=1)
        self.db.add(rule)
        self.db.flush()
        self.audit.record(
            "recurrence.created",
            user_id=user_id,
            entity_type="recurrence",
            entity_id=rule.id,
            changes=model_snapshot(rule, _FIELDS),
        )
        self.generate(rule)
        self.db.commit()
        return rule

    def update(self, user_id: uuid.UUID, rule_id: str, data: RecurrenceInput) -> RecurrenceRule:
        """RN047: altera somente ocorrências FUTURAS ainda previstas; histórico efetivado intacto."""
        rule = self.get(user_id, rule_id, for_update=True)
        if rule.status is RecurrenceStatus.CANCELLED:
            raise BusinessRuleViolation(
                "Recorrência cancelada não pode ser editada.", code="recurrence_cancelled"
            )
        data = self._validate(user_id, data)
        before = model_snapshot(rule, _FIELDS)
        for key, value in data.__dict__.items():
            setattr(rule, key, value)
        changes = diff(before, model_snapshot(rule, _FIELDS))
        today = self.clock.today()
        future_planned = (
            Transaction.recurrence_id == rule.id,
            Transaction.status == TransactionStatus.PLANNED,
            Transaction.occurrence_date >= today,
        )
        if _SCHEDULE_FIELDS & set(changes):
            # Calendário mudou: remove previstas futuras e regenera a partir de hoje.
            self.db.execute(delete(Transaction).where(*future_planned))
            rule.generated_until = today - timedelta(days=1)
        else:
            for tx in self.db.scalars(select(Transaction).where(*future_planned)):
                tx.account_id = rule.account_id
                tx.category_id = rule.category_id
                tx.description = rule.description
                tx.amount_cents = rule.amount_cents
                tx.notes = rule.notes
        self.audit.record(
            "recurrence.updated",
            user_id=user_id,
            entity_type="recurrence",
            entity_id=rule.id,
            changes=changes,
        )
        self.db.flush()
        if rule.status is RecurrenceStatus.ACTIVE:
            self.generate(rule)
        self.db.commit()
        return rule

    def _drop_future_planned(self, rule: RecurrenceRule) -> int:
        result = self.db.execute(
            delete(Transaction).where(
                Transaction.recurrence_id == rule.id,
                Transaction.status == TransactionStatus.PLANNED,
                Transaction.occurrence_date > self.clock.today(),
            )
        )
        rule.generated_until = self.clock.today()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def pause(self, user_id: uuid.UUID, rule_id: str) -> RecurrenceRule:
        rule = self.get(user_id, rule_id, for_update=True)
        if rule.status is not RecurrenceStatus.ACTIVE:
            raise BusinessRuleViolation(
                "Apenas recorrências ativas podem ser pausadas.", code="recurrence_state"
            )
        removed = self._drop_future_planned(rule)
        rule.status = RecurrenceStatus.PAUSED
        self.audit.record(
            "recurrence.paused",
            user_id=user_id,
            entity_type="recurrence",
            entity_id=rule.id,
            changes={"previstas_removidas": removed},
        )
        self.db.commit()
        return rule

    def resume(self, user_id: uuid.UUID, rule_id: str) -> RecurrenceRule:
        rule = self.get(user_id, rule_id, for_update=True)
        if rule.status is not RecurrenceStatus.PAUSED:
            raise BusinessRuleViolation(
                "Apenas recorrências pausadas podem ser retomadas.", code="recurrence_state"
            )
        rule.status = RecurrenceStatus.ACTIVE
        # Não gera retroativamente o período pausado.
        rule.generated_until = max(
            rule.generated_until or self.clock.today(), self.clock.today() - timedelta(days=1)
        )
        self.audit.record(
            "recurrence.resumed", user_id=user_id, entity_type="recurrence", entity_id=rule.id
        )
        self.db.flush()
        self.generate(rule)
        self.db.commit()
        return rule

    def cancel(self, user_id: uuid.UUID, rule_id: str) -> RecurrenceRule:
        rule = self.get(user_id, rule_id, for_update=True)
        if rule.status is RecurrenceStatus.CANCELLED:
            return rule
        removed = self._drop_future_planned(rule)
        rule.status = RecurrenceStatus.CANCELLED
        self.audit.record(
            "recurrence.cancelled",
            user_id=user_id,
            entity_type="recurrence",
            entity_id=rule.id,
            changes={"previstas_removidas": removed},
        )
        self.db.commit()
        return rule

    # ---------------------------------------------------------------- geração
    def generate(self, rule: RecurrenceRule, until: date | None = None) -> GenerationResult:
        """Materializa ocorrências até `until` (padrão: hoje + horizonte). Idempotente.

        Não faz commit — o chamador controla a transação.
        """
        if rule.status is not RecurrenceStatus.ACTIVE:
            return GenerationResult(0, 0)
        today = self.clock.today()
        until = until or today + timedelta(days=self.horizon_days)
        window_start = (rule.generated_until or rule.start_date - timedelta(days=1)) + timedelta(
            days=1
        )
        if window_start > until:
            return GenerationResult(0, 0)
        created = settled = 0
        for day in occurrences_between(
            rule.start_date, rule.frequency, rule.interval, window_start, until, rule.end_date
        ):
            stmt = (
                insert(Transaction)
                .values(
                    id=uuid.uuid4(),
                    user_id=rule.user_id,
                    account_id=rule.account_id,
                    type=rule.type.value,
                    status=TransactionStatus.PLANNED.value,
                    origin=TransactionOrigin.RECURRING.value,
                    description=rule.description,
                    amount_cents=rule.amount_cents,
                    category_id=rule.category_id,
                    competence_date=day,
                    due_date=day,
                    notes=rule.notes,
                    recurrence_id=rule.id,  # RN044
                    occurrence_date=day,
                )
                .on_conflict_do_nothing(
                    index_elements=["recurrence_id", "occurrence_date"],
                    index_where=text("recurrence_id IS NOT NULL"),
                )
                .returning(Transaction.id)
            )
            new_id = self.db.execute(stmt).scalar_one_or_none()
            if new_id is None:
                continue  # já existia (reexecução/concorrência) — RN045
            created += 1
            if rule.auto_settle and day <= today and self._try_settle(rule, new_id, day):
                settled += 1
        rule.generated_until = max(rule.generated_until or until, until)
        if created:
            logger.info(
                "ocorrências geradas",
                extra={"recurrence_id": str(rule.id), "created": created, "settled": settled},
            )
        return GenerationResult(created, settled)

    def _try_settle(self, rule: RecurrenceRule, tx_id: uuid.UUID, day: date) -> bool:
        """Efetivação automática explícita (RN049). Sem saldo, a ocorrência permanece prevista."""
        tx = expect(self.db.get(Transaction, tx_id))
        ledger = Ledger(self.db)
        ledger.lock(rule.user_id, [tx.account_id])
        tx.status = TransactionStatus.SETTLED
        tx.settlement_date = day
        try:
            ledger.apply(tx)  # a verificação ocorre antes de qualquer alteração de saldo
        except InsufficientFunds:
            tx.status = TransactionStatus.PLANNED
            tx.settlement_date = None
            logger.warning(
                "efetivação automática recusada por saldo", extra={"recurrence_id": str(rule.id)}
            )
            return False
        return True
