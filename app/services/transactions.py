"""Lançamentos de receitas e despesas (seção 7, RN011–RN020).

Transferências e pagamentos de fatura também geram linhas em `transactions`, mas são
criados/alterados exclusivamente por seus próprios serviços.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ValidationFailed
from app.domain.ledger import (
    CATEGORIZED_TYPES,
    TransactionOrigin,
    TransactionStatus,
    TransactionType,
)
from app.models import AuditLog, CategoryKind, Transaction
from app.models.base import model_snapshot
from app.models.finance import MAX_CENTS
from app.repositories.base import Page, apply_sort, get_owned, paginate
from app.services.accounts import AccountService
from app.services.audit import AuditService, diff
from app.services.categories import CategoryService
from app.services.idempotency import IdempotencyGuard
from app.services.ledger import Ledger
from app.utils.validation import clean_multiline, clean_text

AUDIT_FIELDS = [
    "type", "status", "account_id", "category_id", "description", "amount_cents",
    "competence_date", "settlement_date", "due_date", "notes",
]

SORTABLE = {
    "competence_date": Transaction.competence_date,
    "settlement_date": Transaction.settlement_date,
    "due_date": Transaction.due_date,
    "amount": Transaction.amount_cents,
    "description": Transaction.description,
    "created_at": Transaction.created_at,
}


@dataclass(frozen=True)
class TransactionInput:
    type: TransactionType
    account_id: str | uuid.UUID
    category_id: str | uuid.UUID | None
    description: str
    amount_cents: int
    competence_date: date
    status: TransactionStatus = TransactionStatus.SETTLED
    settlement_date: date | None = None
    due_date: date | None = None
    notes: str | None = None

    def fingerprint(self) -> dict[str, Any]:
        return {k: (str(v) if v is not None else None) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class TransactionFilters:
    start: date | None = None
    end: date | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    status: TransactionStatus | None = None
    kind: str | None = None  # income | expense | transfer | card_payment
    q: str | None = None
    sort: str | None = None
    page: int = 1
    page_size: int = 25


@dataclass(frozen=True)
class TransactionTotals:
    income_settled: int = 0
    expense_settled: int = 0
    income_planned: int = 0
    expense_planned: int = 0

    @property
    def net_settled(self) -> int:
        return self.income_settled - self.expense_settled


KIND_TYPES: dict[str, list[TransactionType]] = {
    "income": [TransactionType.INCOME],
    "expense": [TransactionType.EXPENSE],
    "transfer": [TransactionType.TRANSFER_IN, TransactionType.TRANSFER_OUT],
    "card_payment": [TransactionType.CARD_PAYMENT],
}


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class TransactionService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.accounts = AccountService(db, clock, audit)
        self.categories = CategoryService(db, clock, audit)

    # ---------------------------------------------------------------- consultas
    def get(self, user_id: uuid.UUID, tx_id: str | uuid.UUID, *, for_update: bool = False) -> Transaction:
        return get_owned(self.db, Transaction, tx_id, user_id, for_update=for_update, message="Lançamento não encontrado.")

    def _filtered(self, user_id: uuid.UUID, f: TransactionFilters) -> Select[Any]:
        stmt = select(Transaction).where(Transaction.user_id == user_id)
        if f.start:
            stmt = stmt.where(Transaction.competence_date >= f.start)
        if f.end:
            stmt = stmt.where(Transaction.competence_date <= f.end)
        if f.account_id:
            stmt = stmt.where(Transaction.account_id == f.account_id)
        if f.category_id:
            stmt = stmt.where(Transaction.category_id == f.category_id)
        if f.status:
            stmt = stmt.where(Transaction.status == f.status)
        if f.kind:
            types = KIND_TYPES.get(f.kind)
            if types is None:
                raise ValidationFailed("Tipo inválido.", code="invalid_filter")
            stmt = stmt.where(Transaction.type.in_(types))
        if f.q:
            term = clean_text(f.q, max_len=100)
            if term:
                stmt = stmt.where(Transaction.description.ilike(f"%{_like_escape(term)}%", escape="\\"))
        return stmt

    def list(self, user_id: uuid.UUID, f: TransactionFilters) -> Page[Transaction]:
        stmt = self._filtered(user_id, f).options(
            joinedload(Transaction.account), joinedload(Transaction.category)
        )
        stmt = apply_sort(stmt, f.sort, SORTABLE, "-competence_date", Transaction.created_at)
        return paginate(self.db, stmt, f.page, f.page_size)

    def totals(self, user_id: uuid.UUID, f: TransactionFilters) -> TransactionTotals:
        """Totalizadores do conjunto filtrado inteiro (não só da página). Exclui transferências."""
        sub = self._filtered(user_id, replace(f, sort=None)).subquery()
        rows = self.db.execute(
            select(sub.c.type, sub.c.status, func.coalesce(func.sum(sub.c.amount_cents), 0))
            .where(sub.c.type.in_(["income", "expense"]), sub.c.status != "cancelled")
            .group_by(sub.c.type, sub.c.status)
        ).all()
        values: dict[str, int] = {}
        for tx_type, status, total in rows:
            values[f"{tx_type}_{status}"] = int(total)
        return TransactionTotals(
            income_settled=values.get("income_settled", 0),
            expense_settled=values.get("expense_settled", 0),
            income_planned=values.get("income_planned", 0),
            expense_planned=values.get("expense_planned", 0),
        )

    def history(self, user_id: uuid.UUID, tx_id: str) -> list[AuditLog]:
        tx = self.get(user_id, tx_id)
        ids = [tx.id]
        if tx.transfer_id:
            ids.append(tx.transfer_id)
        return list(
            self.db.scalars(
                select(AuditLog)
                .where(AuditLog.user_id == user_id, AuditLog.entity_id.in_(ids))
                .order_by(AuditLog.occurred_at, AuditLog.id)
            )
        )

    # ---------------------------------------------------------------- validação
    def _validate(self, user_id: uuid.UUID, data: TransactionInput) -> TransactionInput:
        errors: dict[str, str] = {}
        if data.type not in CATEGORIZED_TYPES:
            raise ValidationFailed("Use a tela de transferências ou de cartões para este tipo.", code="invalid_type")
        description = clean_text(data.description, max_len=140)
        if not description:
            errors["description"] = "Informe a descrição."
        if data.amount_cents <= 0:
            errors["amount"] = "O valor deve ser maior que zero."  # RN011
        elif data.amount_cents > MAX_CENTS:
            errors["amount"] = "Valor acima do limite aceito."
        if data.status is TransactionStatus.CANCELLED:
            errors["status"] = "Use a ação de cancelamento."
        settlement = data.settlement_date
        if data.status is TransactionStatus.SETTLED:
            settlement = settlement or min(data.competence_date, self.clock.today())
            if settlement > self.clock.today():
                errors["settlement_date"] = "Efetivação não pode ser futura. Registre como prevista."
        else:
            settlement = None
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)

        account = self.accounts.require_active(user_id, data.account_id)
        if settlement and settlement < account.start_date:
            raise ValidationFailed(
                "Data de efetivação anterior ao início da conta.",
                field_errors={"settlement_date": f"Conta iniciada em {account.start_date:%d/%m/%Y}"},
            )
        kind = CategoryKind.INCOME if data.type is TransactionType.INCOME else CategoryKind.EXPENSE
        category = self.categories.require_for_type(user_id, data.category_id, kind)  # RN012
        return replace(
            data,
            account_id=account.id,
            category_id=category.id,
            description=description,
            settlement_date=settlement,
            notes=clean_multiline(data.notes, max_len=1000),
        )

    # ---------------------------------------------------------------- escrita
    def create(
        self,
        user_id: uuid.UUID,
        data: TransactionInput,
        *,
        idempotency_key: str,
        origin: TransactionOrigin = TransactionOrigin.MANUAL,
    ) -> Transaction:
        guard = IdempotencyGuard(self.db, self.clock, user_id, idempotency_key, "transaction.create")
        replay = guard.begin(data.fingerprint())
        if replay is not None:
            return self.get(user_id, replay)  # RN017: requisição repetida não reaplica
        data = self._validate(user_id, data)
        ledger = Ledger(self.db)
        ledger.lock(user_id, [data.account_id])  # type: ignore[list-item]
        tx = Transaction(
            user_id=user_id,
            account_id=data.account_id,
            type=data.type,
            status=data.status,
            origin=origin,
            description=data.description,
            amount_cents=data.amount_cents,
            category_id=data.category_id,
            competence_date=data.competence_date,
            settlement_date=data.settlement_date,
            due_date=data.due_date,
            notes=data.notes,
        )
        self.db.add(tx)
        self.db.flush()
        ledger.apply(tx)  # RN014: exatamente um efeito quando efetivado
        guard.complete(tx.id)
        self.audit.record(
            "transaction.created",
            user_id=user_id,
            entity_type="transaction",
            entity_id=tx.id,
            changes=model_snapshot(tx, AUDIT_FIELDS),
        )
        self.db.commit()  # RN018: lançamento + saldo + idempotência + auditoria atômicos
        return tx

    def _require_editable(self, tx: Transaction) -> None:
        if tx.type not in CATEGORIZED_TYPES:
            raise BusinessRuleViolation(
                "Transferências e pagamentos de fatura são alterados em suas próprias telas.",
                code="managed_elsewhere",
            )
        if tx.status is TransactionStatus.CANCELLED:
            raise BusinessRuleViolation("Lançamento cancelado não pode ser alterado.", code="RN015")

    def update(self, user_id: uuid.UUID, tx_id: str, data: TransactionInput) -> Transaction:
        """RN016: estorna o efeito antigo e aplica o novo na mesma transação, auditando a diferença."""
        tx = self.get(user_id, tx_id, for_update=True)
        self._require_editable(tx)
        data = self._validate(user_id, replace(data, type=tx.type))
        before = model_snapshot(tx, AUDIT_FIELDS)
        ledger = Ledger(self.db)
        ledger.lock(user_id, [tx.account_id, data.account_id])  # type: ignore[list-item]
        old_effect = (tx.account_id, -ledger.effect(tx.type, tx.status, tx.amount_cents))
        tx.account_id = data.account_id  # type: ignore[assignment]
        tx.category_id = data.category_id  # type: ignore[assignment]
        tx.description = data.description
        tx.amount_cents = data.amount_cents
        tx.competence_date = data.competence_date
        tx.status = data.status
        tx.settlement_date = data.settlement_date
        tx.due_date = data.due_date
        tx.notes = data.notes
        new_effect = (tx.account_id, ledger.effect(tx.type, tx.status, tx.amount_cents))
        ledger.apply_many([old_effect, new_effect])
        changes = diff(before, model_snapshot(tx, AUDIT_FIELDS))
        if changes:
            self.audit.record(
                "transaction.updated", user_id=user_id, entity_type="transaction", entity_id=tx.id, changes=changes
            )
        self.db.commit()
        return tx

    def settle(self, user_id: uuid.UUID, tx_id: str, settlement_date: date | None = None) -> Transaction:
        tx = self.get(user_id, tx_id, for_update=True)
        self._require_editable(tx)
        if tx.status is TransactionStatus.SETTLED:
            return tx  # idempotente
        when = settlement_date or self.clock.today()
        if when > self.clock.today():
            raise ValidationFailed("Efetivação não pode ser futura.", field_errors={"settlement_date": "Data futura"})
        ledger = Ledger(self.db)
        account = ledger.lock(user_id, [tx.account_id])[tx.account_id]
        if when < account.start_date:
            raise ValidationFailed("Data de efetivação anterior ao início da conta.")
        tx.status = TransactionStatus.SETTLED
        tx.settlement_date = when
        ledger.apply(tx)
        self.audit.record(
            "transaction.settled",
            user_id=user_id,
            entity_type="transaction",
            entity_id=tx.id,
            changes={"status": {"antes": "planned", "depois": "settled"}, "settlement_date": when.isoformat()},
        )
        self.db.commit()
        return tx

    def cancel(self, user_id: uuid.UUID, tx_id: str) -> Transaction:
        """RN015: lançamento efetivado é estornado (saldo revertido), nunca apagado."""
        tx = self.get(user_id, tx_id, for_update=True)
        self._require_editable(tx)
        was_settled = tx.status is TransactionStatus.SETTLED
        ledger = Ledger(self.db)
        ledger.lock(user_id, [tx.account_id])
        ledger.reverse(tx)
        tx.status = TransactionStatus.CANCELLED
        tx.cancelled_at = self.clock.now()
        self.audit.record(
            "transaction.reversed" if was_settled else "transaction.cancelled",
            user_id=user_id,
            entity_type="transaction",
            entity_id=tx.id,
            changes={
                "status": {"antes": "settled" if was_settled else "planned", "depois": "cancelled"},
                "estorno_cents": tx.amount_cents if was_settled else 0,
            },
        )
        self.db.commit()
        return tx


def overdue_filter(today: date) -> Any:
    return and_(
        Transaction.status == TransactionStatus.PLANNED,
        or_(Transaction.due_date < today, and_(Transaction.due_date.is_(None), Transaction.competence_date < today)),
    )
