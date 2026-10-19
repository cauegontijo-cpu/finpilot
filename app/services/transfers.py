"""Transferências entre contas próprias (seção 8, RN021–RN028).

Uma transferência gera duas pernas em `transactions` (saída e entrada) ligadas por
`transfer_id`. Ambas são criadas, efetivadas e canceladas atomicamente, e nunca entram
nos totais de receitas/despesas (RN026).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ValidationFailed
from app.domain.ledger import TransactionOrigin, TransactionStatus, TransactionType
from app.models import Transaction, Transfer
from app.models.finance import MAX_CENTS
from app.repositories.base import Page, get_owned, paginate
from app.services.accounts import AccountService
from app.services.audit import AuditService
from app.services.idempotency import IdempotencyGuard
from app.services.ledger import Ledger
from app.utils.validation import optional_text


@dataclass(frozen=True)
class TransferInput:
    from_account_id: str | uuid.UUID
    to_account_id: str | uuid.UUID
    amount_cents: int
    transfer_date: date
    description: str | None = None
    status: TransactionStatus = TransactionStatus.SETTLED

    def fingerprint(self) -> dict[str, Any]:
        return {k: (str(v) if v is not None else None) for k, v in self.__dict__.items()}


class TransferService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.accounts = AccountService(db, clock, audit)

    def get(self, user_id: uuid.UUID, transfer_id: str | uuid.UUID, *, for_update: bool = False) -> Transfer:
        return get_owned(self.db, Transfer, transfer_id, user_id, for_update=for_update, message="Transferência não encontrada.")

    def list(self, user_id: uuid.UUID, page: int = 1, page_size: int = 25) -> Page[Transfer]:
        stmt = (
            select(Transfer)
            .where(Transfer.user_id == user_id)
            .options(joinedload(Transfer.from_account), joinedload(Transfer.to_account))
            .order_by(Transfer.transfer_date.desc(), Transfer.created_at.desc())
        )
        return paginate(self.db, stmt, page, page_size)

    def legs(self, transfer: Transfer) -> list[Transaction]:
        return list(
            self.db.scalars(
                select(Transaction)
                .where(Transaction.transfer_id == transfer.id, Transaction.user_id == transfer.user_id)
                .order_by(Transaction.type)
                .with_for_update()
            )
        )

    def _validate(self, user_id: uuid.UUID, data: TransferInput) -> tuple[uuid.UUID, uuid.UUID, str | None]:
        errors: dict[str, str] = {}
        if data.amount_cents <= 0:
            errors["amount"] = "O valor deve ser maior que zero."  # RN023
        elif data.amount_cents > MAX_CENTS:
            errors["amount"] = "Valor acima do limite aceito."
        if str(data.from_account_id) == str(data.to_account_id):
            errors["to_account_id"] = "Origem e destino devem ser diferentes."  # RN021
        if data.status is TransactionStatus.SETTLED and data.transfer_date > self.clock.today():
            errors["transfer_date"] = "Transferência efetivada não pode ter data futura. Agende como prevista."
        if data.status is TransactionStatus.CANCELLED:
            errors["status"] = "Status inválido."
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)
        source = self.accounts.require_active(user_id, data.from_account_id)  # RN022
        target = self.accounts.require_active(user_id, data.to_account_id)
        if data.status is TransactionStatus.SETTLED:
            for account in (source, target):
                if data.transfer_date < account.start_date:
                    raise ValidationFailed(
                        f"Data anterior ao início da conta “{account.name}”.",
                        field_errors={"transfer_date": "Data inválida"},
                    )
        return source.id, target.id, optional_text(data.description, max_len=140)

    def create(self, user_id: uuid.UUID, data: TransferInput, *, idempotency_key: str) -> Transfer:
        guard = IdempotencyGuard(self.db, self.clock, user_id, idempotency_key, "transfer.create")
        replay = guard.begin(data.fingerprint())
        if replay is not None:
            return self.get(user_id, replay)  # RN028
        source_id, target_id, description = self._validate(user_id, data)
        ledger = Ledger(self.db)
        accounts = ledger.lock(user_id, [source_id, target_id])  # RN010: lock em ordem de id
        transfer = Transfer(
            user_id=user_id,
            from_account_id=source_id,
            to_account_id=target_id,
            amount_cents=data.amount_cents,
            transfer_date=data.transfer_date,
            description=description,
            status=data.status,
        )
        self.db.add(transfer)
        self.db.flush()
        label = description or "Transferência"
        settled = data.status is TransactionStatus.SETTLED
        out_leg = Transaction(
            user_id=user_id,
            account_id=source_id,
            type=TransactionType.TRANSFER_OUT,
            status=data.status,
            origin=TransactionOrigin.TRANSFER,
            description=f"{label} → {accounts[target_id].name}"[:140],
            amount_cents=data.amount_cents,
            competence_date=data.transfer_date,
            settlement_date=data.transfer_date if settled else None,
            transfer_id=transfer.id,
        )
        in_leg = Transaction(
            user_id=user_id,
            account_id=target_id,
            type=TransactionType.TRANSFER_IN,
            status=data.status,
            origin=TransactionOrigin.TRANSFER,
            description=f"{label} ← {accounts[source_id].name}"[:140],
            amount_cents=data.amount_cents,
            competence_date=data.transfer_date,
            settlement_date=data.transfer_date if settled else None,
            transfer_id=transfer.id,
        )
        self.db.add_all([out_leg, in_leg])
        self.db.flush()
        # RN024/RN025: as duas pernas no mesmo commit; qualquer falha (ex.: saldo) desfaz tudo.
        ledger.apply_many(
            [(source_id, ledger.effect(out_leg.type, out_leg.status, out_leg.amount_cents)),
             (target_id, ledger.effect(in_leg.type, in_leg.status, in_leg.amount_cents))]
        )
        guard.complete(transfer.id)
        self.audit.record(
            "transfer.created",
            user_id=user_id,
            entity_type="transfer",
            entity_id=transfer.id,
            changes={
                "from_account_id": str(source_id),
                "to_account_id": str(target_id),
                "amount_cents": data.amount_cents,
                "transfer_date": data.transfer_date.isoformat(),
                "status": data.status.value,
            },
        )
        self.db.commit()
        return transfer

    def settle(self, user_id: uuid.UUID, transfer_id: str) -> Transfer:
        transfer = self.get(user_id, transfer_id, for_update=True)
        if transfer.status is TransactionStatus.SETTLED:
            return transfer
        if transfer.status is TransactionStatus.CANCELLED:
            raise BusinessRuleViolation("Transferência cancelada.", code="RN027")
        today = self.clock.today()
        when = min(transfer.transfer_date, today)
        ledger = Ledger(self.db)
        ledger.lock(user_id, [transfer.from_account_id, transfer.to_account_id])
        legs = self.legs(transfer)
        for leg in legs:
            leg.status = TransactionStatus.SETTLED
            leg.settlement_date = when
        ledger.apply_many([(leg.account_id, ledger.effect(leg.type, leg.status, leg.amount_cents)) for leg in legs])
        transfer.status = TransactionStatus.SETTLED
        self.audit.record("transfer.settled", user_id=user_id, entity_type="transfer", entity_id=transfer.id)
        self.db.commit()
        return transfer

    def cancel(self, user_id: uuid.UUID, transfer_id: str) -> Transfer:
        """RN027: estorna os dois lados de forma consistente, preservando o histórico."""
        transfer = self.get(user_id, transfer_id, for_update=True)
        if transfer.status is TransactionStatus.CANCELLED:
            return transfer
        was_settled = transfer.status is TransactionStatus.SETTLED
        ledger = Ledger(self.db)
        ledger.lock(user_id, [transfer.from_account_id, transfer.to_account_id])
        legs = self.legs(transfer)
        ledger.apply_many(
            [(leg.account_id, -ledger.effect(leg.type, leg.status, leg.amount_cents)) for leg in legs]
        )
        now = self.clock.now()
        for leg in legs:
            leg.status = TransactionStatus.CANCELLED
            leg.cancelled_at = now
        transfer.status = TransactionStatus.CANCELLED
        transfer.cancelled_at = now
        self.audit.record(
            "transfer.reversed" if was_settled else "transfer.cancelled",
            user_id=user_id,
            entity_type="transfer",
            entity_id=transfer.id,
            changes={"amount_cents": transfer.amount_cents, "estornado": was_settled},
        )
        self.db.commit()
        return transfer
