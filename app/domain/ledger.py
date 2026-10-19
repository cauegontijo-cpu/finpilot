"""Tipos de lançamento e seus efeitos sobre o saldo realizado (RN004–RN007, RN013–RN014)."""

from __future__ import annotations

from enum import StrEnum


class TransactionType(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    CARD_PAYMENT = "card_payment"


class TransactionStatus(StrEnum):
    PLANNED = "planned"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class TransactionOrigin(StrEnum):
    MANUAL = "manual"
    IMPORTED = "imported"
    RECURRING = "recurring"
    TRANSFER = "transfer"
    CARD = "card"


TYPE_LABELS = {
    TransactionType.INCOME: "Receita",
    TransactionType.EXPENSE: "Despesa",
    TransactionType.TRANSFER_OUT: "Transferência (saída)",
    TransactionType.TRANSFER_IN: "Transferência (entrada)",
    TransactionType.CARD_PAYMENT: "Pagamento de fatura",
}
STATUS_LABELS = {
    TransactionStatus.PLANNED: "Prevista",
    TransactionStatus.SETTLED: "Efetivada",
    TransactionStatus.CANCELLED: "Cancelada",
}
ORIGIN_LABELS = {
    TransactionOrigin.MANUAL: "Manual",
    TransactionOrigin.IMPORTED: "Importada",
    TransactionOrigin.RECURRING: "Recorrente",
    TransactionOrigin.TRANSFER: "Transferência",
    TransactionOrigin.CARD: "Cartão",
}

#: Tipos que geram receita ou despesa (transferências e pagamentos de fatura não geram).
CATEGORIZED_TYPES = frozenset({TransactionType.INCOME, TransactionType.EXPENSE})
INFLOW_TYPES = frozenset({TransactionType.INCOME, TransactionType.TRANSFER_IN})


def signed_effect(tx_type: TransactionType, amount_cents: int) -> int:
    """Efeito no saldo da conta de um lançamento EFETIVADO."""
    if amount_cents <= 0:
        raise ValueError("valor deve ser positivo")
    return amount_cents if tx_type in INFLOW_TYPES else -amount_cents


def balance_effect(tx_type: TransactionType, status: TransactionStatus, amount_cents: int) -> int:
    """Somente lançamentos efetivados alteram o saldo realizado (RN004/RN013)."""
    if status is not TransactionStatus.SETTLED:
        return 0
    return signed_effect(tx_type, amount_cents)
