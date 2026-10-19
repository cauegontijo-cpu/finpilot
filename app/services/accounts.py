"""Contas financeiras (seção 6, RN001–RN010)."""

from __future__ import annotations

import builtins
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ConflictError, ValidationFailed
from app.models import (
    Account,
    AccountType,
    CardPayment,
    ImportBatch,
    RecurrenceRule,
    Transaction,
    Transfer,
)
from app.models.base import model_snapshot
from app.models.finance import MAX_CENTS
from app.repositories.base import get_owned
from app.services.audit import AuditService, diff
from app.services.ledger import Ledger
from app.utils.validation import clean_text, optional_text, validate_color, validate_icon

_AUDIT_FIELDS = [
    "name",
    "type",
    "institution",
    "color",
    "icon",
    "initial_balance_cents",
    "start_date",
    "allow_negative",
    "archived_at",
]


@dataclass(frozen=True)
class AccountInput:
    name: str
    type: AccountType
    initial_balance_cents: int
    start_date: date
    allow_negative: bool = False
    institution: str | None = None
    color: str = "#2563eb"
    icon: str = "wallet"


@dataclass
class AccountSummary:
    accounts: builtins.list[Account] = field(default_factory=list)
    total_cents: int = 0


class AccountService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit

    # ---------------------------------------------------------------- consultas
    def list(self, user_id: uuid.UUID, *, include_archived: bool = False) -> builtins.list[Account]:
        stmt = select(Account).where(Account.user_id == user_id)
        if not include_archived:
            stmt = stmt.where(Account.archived_at.is_(None))
        return list(
            self.db.scalars(
                stmt.order_by(Account.archived_at.is_not(None), func.lower(Account.name))
            )
        )

    def summary(self, user_id: uuid.UUID) -> AccountSummary:
        accounts = self.list(user_id)
        return AccountSummary(
            accounts=accounts, total_cents=sum(a.current_balance_cents for a in accounts)
        )

    def get(
        self, user_id: uuid.UUID, account_id: str | uuid.UUID, *, for_update: bool = False
    ) -> Account:
        return get_owned(
            self.db,
            Account,
            account_id,
            user_id,
            for_update=for_update,
            message="Conta não encontrada.",
        )

    def require_active(self, user_id: uuid.UUID, account_id: str | uuid.UUID | None) -> Account:
        if account_id in (None, ""):
            raise ValidationFailed(
                "Selecione uma conta.", field_errors={"account_id": "Obrigatória"}
            )
        account = self.get(user_id, account_id)
        if not account.is_active:
            raise BusinessRuleViolation(
                "Conta arquivada não aceita novas movimentações.", code="account_archived"
            )
        return account

    def has_history(self, account_id: uuid.UUID) -> bool:
        checks = [
            exists().where(Transaction.account_id == account_id),
            exists().where(
                (Transfer.from_account_id == account_id) | (Transfer.to_account_id == account_id)
            ),
            exists().where(RecurrenceRule.account_id == account_id),
            exists().where(CardPayment.account_id == account_id),
            exists().where(ImportBatch.account_id == account_id),
        ]
        return any(self.db.scalar(select(c)) for c in checks)

    # ---------------------------------------------------------------- escrita
    def _validate(self, data: AccountInput) -> AccountInput:
        errors: dict[str, str] = {}
        name = clean_text(data.name, max_len=60)
        if not name:
            errors["name"] = "Informe o nome da conta."
        if abs(data.initial_balance_cents) > MAX_CENTS:
            errors["initial_balance"] = "Valor fora do limite aceito."
        if data.start_date > self.clock.today():
            errors["start_date"] = "A data de início não pode ser futura."
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)
        return AccountInput(
            name=name,
            type=data.type,
            initial_balance_cents=data.initial_balance_cents,
            start_date=data.start_date,
            allow_negative=data.allow_negative,
            institution=optional_text(data.institution, max_len=80),
            color=validate_color(data.color, "#2563eb"),
            icon=validate_icon(data.icon, "wallet"),
        )

    def create(self, user_id: uuid.UUID, data: AccountInput) -> Account:
        data = self._validate(data)
        if data.initial_balance_cents < 0 and not data.allow_negative:
            raise BusinessRuleViolation(
                "Saldo inicial negativo exige permitir saldo negativo na conta.", code="RN008"
            )
        account = Account(
            user_id=user_id,
            current_balance_cents=data.initial_balance_cents,  # RN003: não é receita
            **data.__dict__,
        )
        self.db.add(account)
        self._flush_unique()
        self.audit.record(
            "account.created",
            user_id=user_id,
            entity_type="account",
            entity_id=account.id,
            changes=model_snapshot(account, _AUDIT_FIELDS),
        )
        self.db.commit()
        return account

    def update(self, user_id: uuid.UUID, account_id: str, data: AccountInput) -> Account:
        """Alteração sensível: mudar o saldo inicial ajusta o saldo atual pela diferença (auditado)."""
        data = self._validate(data)
        ledger = Ledger(self.db)
        account = self.get(user_id, account_id)
        ledger.lock(user_id, [account.id])
        before = model_snapshot(account, _AUDIT_FIELDS)
        delta = data.initial_balance_cents - account.initial_balance_cents
        earliest = self.db.scalar(
            select(func.min(Transaction.settlement_date)).where(
                Transaction.account_id == account.id, Transaction.status == "settled"
            )
        )
        if earliest is not None and data.start_date > earliest:
            raise ValidationFailed(
                "A data de início não pode ser posterior a lançamentos já efetivados.",
                field_errors={"start_date": f"Há lançamento efetivado em {earliest:%d/%m/%Y}"},
            )
        for key, value in data.__dict__.items():
            if key != "initial_balance_cents":
                setattr(account, key, value)
        account.initial_balance_cents = data.initial_balance_cents
        ledger.apply_delta(account.id, delta)
        if account.current_balance_cents < 0 and not account.allow_negative:
            raise BusinessRuleViolation(
                "A conta ficaria com saldo negativo sem permissão para isso.", code="RN008"
            )
        self._flush_unique()
        self.audit.record(
            "account.updated",
            user_id=user_id,
            entity_type="account",
            entity_id=account.id,
            changes=diff(before, model_snapshot(account, _AUDIT_FIELDS)),
        )
        self.db.commit()
        return account

    def set_archived(self, user_id: uuid.UUID, account_id: str, archived: bool) -> Account:
        account = self.get(user_id, account_id)
        account.archived_at = self.clock.now() if archived else None
        self._flush_unique()
        self.audit.record(
            "account.archived" if archived else "account.restored",
            user_id=user_id,
            entity_type="account",
            entity_id=account.id,
        )
        self.db.commit()
        return account

    def delete(self, user_id: uuid.UUID, account_id: str) -> None:
        """RN009: exclusão física apenas sem histórico — caso contrário, arquivar."""
        account = self.get(user_id, account_id, for_update=True)
        if self.has_history(account.id):
            raise BusinessRuleViolation(
                "Esta conta possui histórico financeiro e não pode ser excluída. Arquive-a.",
                code="RN009",
            )
        self.audit.record(
            "account.deleted",
            user_id=user_id,
            entity_type="account",
            entity_id=account.id,
            changes=model_snapshot(account, _AUDIT_FIELDS),
        )
        self.db.delete(account)
        self.db.commit()

    def _flush_unique(self) -> None:
        # Flush direto: em caso de violação, a transação inteira é desfeita (a operação falhou).
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError(
                "Já existe uma conta ativa com esse nome.",
                code="duplicate_account",
                field_errors={"name": "Nome já utilizado"},
            ) from exc
