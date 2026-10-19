"""Único ponto que altera saldos de contas (ADR-005).

Regras garantidas aqui:
* RN004/RN013 — somente lançamentos efetivados alteram o saldo realizado;
* RN008 — contas sem saldo negativo recusam operações que excedam o saldo;
* RN010/RN018 — contas são travadas com SELECT … FOR UPDATE (ordem determinística por id,
  evitando deadlocks) dentro da transação do banco que contém a operação.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, InsufficientFunds, NotFoundError
from app.domain.ledger import TransactionStatus, TransactionType, balance_effect
from app.models import Account, Transaction
from app.models.finance import MAX_CENTS


class Ledger:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._locked: dict[uuid.UUID, Account] = {}

    def lock(self, user_id: uuid.UUID, account_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, Account]:
        ids = sorted({a for a in account_ids if a is not None} - set(self._locked))
        if ids:
            rows = self.db.scalars(
                select(Account)
                .where(Account.id.in_(ids), Account.user_id == user_id)
                .order_by(Account.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            if len(rows) != len(ids):
                raise NotFoundError("Conta não encontrada.")
            self._locked.update({a.id: a for a in rows})
        return self._locked

    def account(self, account_id: uuid.UUID) -> Account:
        try:
            return self._locked[account_id]
        except KeyError as exc:  # pragma: no cover - erro de programação
            raise RuntimeError("conta não travada antes do uso") from exc

    def apply_delta(self, account_id: uuid.UUID, delta: int) -> None:
        if delta == 0:
            return
        account = self.account(account_id)
        new_balance = account.current_balance_cents + delta
        if abs(new_balance) > MAX_CENTS:
            raise BusinessRuleViolation("Saldo resultante fora do limite aceito.", code="balance_range")
        if delta < 0 and not account.allow_negative and new_balance < 0:
            raise InsufficientFunds(account.name)
        account.current_balance_cents = new_balance

    def apply(self, tx: Transaction) -> None:
        self.apply_delta(tx.account_id, balance_effect(tx.type, tx.status, tx.amount_cents))

    def reverse(self, tx: Transaction) -> None:
        self.apply_delta(tx.account_id, -balance_effect(tx.type, tx.status, tx.amount_cents))

    @staticmethod
    def effect(tx_type: TransactionType, status: TransactionStatus, amount: int) -> int:
        return balance_effect(tx_type, status, amount)

    def apply_many(self, changes: list[tuple[uuid.UUID, int]]) -> None:
        """Aplica vários deltas consolidando por conta (ex.: edição = estorno + novo efeito).

        A verificação de saldo negativo considera o efeito líquido da operação na conta.
        """
        net: dict[uuid.UUID, int] = {}
        for account_id, delta in changes:
            net[account_id] = net.get(account_id, 0) + delta
        for account_id in sorted(net, key=lambda a: (net[a] < 0, str(a))):
            self.apply_delta(account_id, net[account_id])
