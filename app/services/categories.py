"""Categorias de receitas e despesas (seção 9)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ConflictError, ValidationFailed
from app.models import Budget, CardPurchase, Category, CategoryKind, RecurrenceRule, Transaction
from app.models.base import model_snapshot
from app.repositories.base import get_owned
from app.services.audit import AuditService, diff
from app.utils.validation import clean_text, validate_color, validate_icon

DEFAULT_CATEGORIES: list[tuple[str, CategoryKind, str, str]] = [
    ("Alimentação", CategoryKind.EXPENSE, "#f97316", "utensils"),
    ("Moradia", CategoryKind.EXPENSE, "#0ea5e9", "house"),
    ("Transporte", CategoryKind.EXPENSE, "#eab308", "car"),
    ("Saúde", CategoryKind.EXPENSE, "#ef4444", "heart-pulse"),
    ("Educação", CategoryKind.EXPENSE, "#6366f1", "graduation-cap"),
    ("Lazer", CategoryKind.EXPENSE, "#ec4899", "gamepad-2"),
    ("Assinaturas", CategoryKind.EXPENSE, "#8b5cf6", "tv"),
    ("Compras", CategoryKind.EXPENSE, "#14b8a6", "shopping-bag"),
    ("Outros", CategoryKind.EXPENSE, "#64748b", "tag"),
    ("Salário", CategoryKind.INCOME, "#10b981", "briefcase"),
    ("Freelance", CategoryKind.INCOME, "#22c55e", "laptop"),
    ("Investimentos", CategoryKind.INCOME, "#0d9488", "chart-line"),
    ("Reembolsos", CategoryKind.INCOME, "#84cc16", "undo-2"),
    ("Outras receitas", CategoryKind.INCOME, "#059669", "coins"),
]

KIND_LABELS = {CategoryKind.INCOME: "Receita", CategoryKind.EXPENSE: "Despesa"}
_AUDIT_FIELDS = ["name", "kind", "color", "icon", "archived_at"]


@dataclass(frozen=True)
class CategoryInput:
    name: str
    kind: CategoryKind
    color: str
    icon: str


class CategoryService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit

    def create_defaults(self, user_id: uuid.UUID) -> None:
        for name, kind, color, icon in DEFAULT_CATEGORIES:
            self.db.add(Category(user_id=user_id, name=name, kind=kind, color=color, icon=icon))

    def list(
        self, user_id: uuid.UUID, *, kind: CategoryKind | None = None, include_archived: bool = False
    ) -> list[Category]:
        stmt = select(Category).where(Category.user_id == user_id)
        if kind is not None:
            stmt = stmt.where(Category.kind == kind)
        if not include_archived:
            stmt = stmt.where(Category.archived_at.is_(None))
        return list(self.db.scalars(stmt.order_by(Category.kind, func.lower(Category.name))))

    def get(self, user_id: uuid.UUID, category_id: str | uuid.UUID) -> Category:
        return get_owned(self.db, Category, category_id, user_id, message="Categoria não encontrada.")

    def require_for_type(
        self, user_id: uuid.UUID, category_id: str | uuid.UUID | None, kind: CategoryKind
    ) -> Category:
        """RN012: a categoria deve pertencer ao usuário, estar ativa e ser do tipo correto."""
        if category_id in (None, ""):
            raise ValidationFailed("Selecione uma categoria.", field_errors={"category_id": "Obrigatória"})
        category = self.get(user_id, category_id)  # type: ignore[arg-type]
        if category.kind is not kind:
            raise BusinessRuleViolation(
                f"A categoria “{category.name}” não é de {KIND_LABELS[kind].lower()}.", code="RN012"
            )
        if not category.is_active:
            raise BusinessRuleViolation("Categoria arquivada não aceita novos lançamentos.", code="RN012")
        return category

    def _validate(self, data: CategoryInput) -> CategoryInput:
        name = clean_text(data.name, max_len=50)
        if not name:
            raise ValidationFailed("Informe o nome.", field_errors={"name": "Obrigatório"})
        return CategoryInput(
            name=name, kind=data.kind, color=validate_color(data.color), icon=validate_icon(data.icon)
        )

    def create(self, user_id: uuid.UUID, data: CategoryInput) -> Category:
        data = self._validate(data)
        category = Category(user_id=user_id, **data.__dict__)
        self.db.add(category)
        self._flush_unique()
        self.audit.record(
            "category.created",
            user_id=user_id,
            entity_type="category",
            entity_id=category.id,
            changes=model_snapshot(category, _AUDIT_FIELDS),
        )
        self.db.commit()
        return category

    def update(self, user_id: uuid.UUID, category_id: str, data: CategoryInput) -> Category:
        category = self.get(user_id, category_id)
        data = self._validate(data)
        if data.kind is not category.kind and self._in_use(category.id):
            raise BusinessRuleViolation(
                "Não é possível alterar o tipo de uma categoria com lançamentos.", code="category_in_use"
            )
        before = model_snapshot(category, _AUDIT_FIELDS)
        for key, value in data.__dict__.items():
            setattr(category, key, value)
        self._flush_unique()
        self.audit.record(
            "category.updated",
            user_id=user_id,
            entity_type="category",
            entity_id=category.id,
            changes=diff(before, model_snapshot(category, _AUDIT_FIELDS)),
        )
        self.db.commit()
        return category

    def set_archived(self, user_id: uuid.UUID, category_id: str, archived: bool) -> Category:
        """Arquivamento preserva o histórico: lançamentos antigos mantêm a referência."""
        category = self.get(user_id, category_id)
        category.archived_at = self.clock.now() if archived else None
        self._flush_unique()
        self.audit.record(
            "category.archived" if archived else "category.restored",
            user_id=user_id,
            entity_type="category",
            entity_id=category.id,
        )
        self.db.commit()
        return category

    def delete(self, user_id: uuid.UUID, category_id: str) -> None:
        """Exclusão física somente sem histórico; caso contrário, arquive."""
        category = self.get(user_id, category_id)
        if self._in_use(category.id):
            raise BusinessRuleViolation(
                "Categoria com histórico não pode ser excluída. Use o arquivamento.",
                code="category_in_use",
            )
        self.audit.record(
            "category.deleted",
            user_id=user_id,
            entity_type="category",
            entity_id=category.id,
            changes=model_snapshot(category, _AUDIT_FIELDS),
        )
        self.db.delete(category)
        self.db.commit()

    def _in_use(self, category_id: uuid.UUID) -> bool:
        checks = [
            exists().where(Transaction.category_id == category_id),
            exists().where(Budget.category_id == category_id),
            exists().where(RecurrenceRule.category_id == category_id),
            exists().where(CardPurchase.category_id == category_id),
        ]
        return any(self.db.scalar(select(c)) for c in checks)

    def _flush_unique(self) -> None:
        # Flush direto: em caso de violação, a transação inteira é desfeita (a operação falhou).
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError(
                "Já existe uma categoria ativa com esse nome e tipo.",
                code="duplicate_category",
                field_errors={"name": "Nome já utilizado"},
            ) from exc
