"""Importa todos os modelos para registro no metadata (Alembic e relacionamentos)."""

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.cards import (
    CardInstallment,
    CardPayment,
    CardPurchase,
    CardStatement,
    CreditCard,
    InstallmentKind,
    InstallmentStatus,
    PaymentStatus,
    PurchaseStatus,
)
from app.models.finance import (
    ACCOUNT_TYPE_LABELS,
    Account,
    AccountType,
    Category,
    CategoryKind,
    Transaction,
    Transfer,
)
from app.models.imports import ImportBatch, ImportRow, ImportRowStatus, ImportStatus
from app.models.planning import (
    Budget,
    Goal,
    GoalAllocation,
    GoalStatus,
    RecurrenceRule,
    RecurrenceStatus,
)
from app.models.user import (
    EmailToken,
    EmailTokenPurpose,
    IdempotencyKey,
    PasswordResetToken,
    User,
    UserSession,
)

__all__ = [
    "ACCOUNT_TYPE_LABELS",
    "Account",
    "AccountType",
    "AuditLog",
    "Base",
    "Budget",
    "CardInstallment",
    "CardPayment",
    "CardPurchase",
    "CardStatement",
    "Category",
    "CategoryKind",
    "CreditCard",
    "EmailToken",
    "EmailTokenPurpose",
    "Goal",
    "GoalAllocation",
    "GoalStatus",
    "IdempotencyKey",
    "ImportBatch",
    "ImportRow",
    "ImportRowStatus",
    "ImportStatus",
    "InstallmentKind",
    "InstallmentStatus",
    "PasswordResetToken",
    "PaymentStatus",
    "PurchaseStatus",
    "RecurrenceRule",
    "RecurrenceStatus",
    "Transaction",
    "Transfer",
    "User",
    "UserSession",
]
