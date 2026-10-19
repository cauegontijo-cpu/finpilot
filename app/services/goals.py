"""Metas de economia (seção 11, RN038–RN043).

Alocações são RESERVAS VIRTUAIS: não criam lançamentos nem alteram saldos bancários
(RN039/RN040). O valor alocado e o progresso são sempre calculados por agregação (RN043).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ValidationFailed
from app.domain.dates import Month, add_months, clamp_day
from app.domain.money import percent
from app.models import Account, Goal, GoalAllocation, GoalStatus
from app.models.base import model_snapshot
from app.models.finance import MAX_CENTS
from app.repositories.base import get_owned
from app.services.audit import AuditService, diff
from app.utils.validation import clean_multiline, clean_text, validate_color, validate_icon

_FIELDS = ["name", "description", "target_cents", "deadline", "status", "color", "icon"]


@dataclass(frozen=True)
class GoalInput:
    name: str
    target_cents: int
    deadline: date | None = None
    description: str | None = None
    color: str = "#7c3aed"
    icon: str = "target"


@dataclass
class GoalProgress:
    goal: Goal
    allocated: int
    monthly_average: int  # média das alocações líquidas dos últimos 3 meses
    today: date

    @property
    def remaining(self) -> int:
        return max(0, self.goal.target_cents - self.allocated)

    @property
    def pct(self) -> Decimal:
        return percent(self.allocated, self.goal.target_cents)

    @property
    def reached(self) -> bool:
        return self.allocated >= self.goal.target_cents

    @property
    def estimated_completion(self) -> date | None:
        """Estimativa linear pela média recente; None se não houver ritmo positivo."""
        if self.reached:
            return self.today
        if self.monthly_average <= 0:
            return None
        months = math.ceil(self.remaining / self.monthly_average)
        if months > 600:
            return None
        year, month = add_months(self.today.year, self.today.month, months)
        return clamp_day(year, month, self.today.day)

    @property
    def required_monthly(self) -> int | None:
        """Quanto reservar por mês para cumprir o prazo."""
        deadline = self.goal.deadline
        if deadline is None or self.reached:
            return None
        months = (deadline.year - self.today.year) * 12 + deadline.month - self.today.month
        if months <= 0:
            return self.remaining
        return math.ceil(self.remaining / months)

    @property
    def on_track(self) -> bool | None:
        required = self.required_monthly
        if required is None:
            return None
        return self.monthly_average >= required


@dataclass
class ReservationSummary:
    available_cents: int
    reserved_cents: int

    @property
    def free_cents(self) -> int:
        return self.available_cents - self.reserved_cents

    @property
    def over_reserved(self) -> bool:
        """RN042: reservas virtuais acima da disponibilidade real."""
        return self.reserved_cents > self.available_cents


class GoalService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit

    def get(self, user_id: uuid.UUID, goal_id: str | uuid.UUID, *, for_update: bool = False) -> Goal:
        return get_owned(self.db, Goal, goal_id, user_id, for_update=for_update, message="Meta não encontrada.")

    def _allocated(self, goal_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not goal_ids:
            return {}
        rows = self.db.execute(
            select(GoalAllocation.goal_id, func.sum(GoalAllocation.amount_cents))
            .where(GoalAllocation.goal_id.in_(goal_ids))
            .group_by(GoalAllocation.goal_id)
        )
        return {gid: int(total) for gid, total in rows}

    def _recent_average(self, goal_ids: list[uuid.UUID], today: date) -> dict[uuid.UUID, int]:
        if not goal_ids:
            return {}
        since = Month.of(today).shift(-2).first_day
        rows = self.db.execute(
            select(GoalAllocation.goal_id, func.sum(GoalAllocation.amount_cents))
            .where(GoalAllocation.goal_id.in_(goal_ids), GoalAllocation.allocation_date >= since)
            .group_by(GoalAllocation.goal_id)
        )
        return {gid: int(total) // 3 for gid, total in rows}

    def list(self, user_id: uuid.UUID, *, include_archived: bool = False) -> list[GoalProgress]:
        stmt = select(Goal).where(Goal.user_id == user_id)
        if not include_archived:
            stmt = stmt.where(Goal.status != GoalStatus.ARCHIVED)
        goals = list(self.db.scalars(stmt.order_by(Goal.status, Goal.deadline.nulls_last(), Goal.name)))
        ids = [g.id for g in goals]
        allocated = self._allocated(ids)
        averages = self._recent_average(ids, self.clock.today())
        today = self.clock.today()
        return [GoalProgress(g, allocated.get(g.id, 0), averages.get(g.id, 0), today) for g in goals]

    def progress(self, user_id: uuid.UUID, goal_id: str) -> GoalProgress:
        goal = self.get(user_id, goal_id)
        today = self.clock.today()
        return GoalProgress(
            goal, self._allocated([goal.id]).get(goal.id, 0), self._recent_average([goal.id], today).get(goal.id, 0), today
        )

    def allocations(self, user_id: uuid.UUID, goal_id: str) -> list[GoalAllocation]:
        goal = self.get(user_id, goal_id)
        return list(
            self.db.scalars(
                select(GoalAllocation)
                .where(GoalAllocation.goal_id == goal.id)
                .order_by(GoalAllocation.allocation_date.desc(), GoalAllocation.created_at.desc())
            )
        )

    def reservation_summary(self, user_id: uuid.UUID) -> ReservationSummary:
        """RN041: separa dinheiro disponível nas contas do valor reservado virtualmente."""
        available = int(
            self.db.scalar(
                select(func.coalesce(func.sum(Account.current_balance_cents), 0)).where(
                    Account.user_id == user_id, Account.archived_at.is_(None)
                )
            )
            or 0
        )
        reserved = int(
            self.db.scalar(
                select(func.coalesce(func.sum(GoalAllocation.amount_cents), 0))
                .join(Goal, Goal.id == GoalAllocation.goal_id)
                .where(GoalAllocation.user_id == user_id, Goal.status != GoalStatus.ARCHIVED)
            )
            or 0
        )
        return ReservationSummary(available_cents=available, reserved_cents=reserved)

    # ---------------------------------------------------------------- escrita
    def _validate(self, data: GoalInput, *, allow_past_deadline: bool = False) -> GoalInput:
        errors: dict[str, str] = {}
        name = clean_text(data.name, max_len=80)
        if not name:
            errors["name"] = "Informe o nome da meta."
        if data.target_cents <= 0 or data.target_cents > MAX_CENTS:
            errors["target"] = "O valor desejado deve ser positivo."  # RN038
        if data.deadline and data.deadline < self.clock.today() and not allow_past_deadline:
            errors["deadline"] = "O prazo não pode estar no passado."
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)
        return GoalInput(
            name=name,
            target_cents=data.target_cents,
            deadline=data.deadline,
            description=clean_multiline(data.description, max_len=1000),
            color=validate_color(data.color, "#7c3aed"),
            icon=validate_icon(data.icon, "target"),
        )

    def create(self, user_id: uuid.UUID, data: GoalInput) -> Goal:
        data = self._validate(data)
        goal = Goal(user_id=user_id, status=GoalStatus.ACTIVE, **data.__dict__)
        self.db.add(goal)
        self.db.flush()
        self.audit.record("goal.created", user_id=user_id, entity_type="goal", entity_id=goal.id, changes=model_snapshot(goal, _FIELDS))
        self.db.commit()
        return goal

    def update(self, user_id: uuid.UUID, goal_id: str, data: GoalInput) -> Goal:
        goal = self.get(user_id, goal_id, for_update=True)
        # Prazo já vencido pode ser mantido; só não se aceita definir um novo prazo no passado.
        data = self._validate(data, allow_past_deadline=data.deadline == goal.deadline)
        before = model_snapshot(goal, _FIELDS)
        for key, value in data.__dict__.items():
            setattr(goal, key, value)
        self._refresh_status(goal)
        self.audit.record(
            "goal.updated", user_id=user_id, entity_type="goal", entity_id=goal.id,
            changes=diff(before, model_snapshot(goal, _FIELDS)),
        )
        self.db.commit()
        return goal

    def set_archived(self, user_id: uuid.UUID, goal_id: str, archived: bool) -> Goal:
        goal = self.get(user_id, goal_id, for_update=True)
        goal.status = GoalStatus.ARCHIVED if archived else GoalStatus.ACTIVE
        if not archived:
            self._refresh_status(goal)
        self.audit.record("goal.archived" if archived else "goal.restored", user_id=user_id, entity_type="goal", entity_id=goal.id)
        self.db.commit()
        return goal

    def allocate(self, user_id: uuid.UUID, goal_id: str, amount_cents: int, when: date | None = None, note: str | None = None) -> GoalAllocation:
        goal = self.get(user_id, goal_id, for_update=True)
        if goal.status is GoalStatus.ARCHIVED:
            raise BusinessRuleViolation("Meta arquivada não recebe alocações.", code="goal_archived")
        if amount_cents <= 0 or amount_cents > MAX_CENTS:
            raise ValidationFailed("Informe um valor positivo.", field_errors={"amount": "Valor inválido"})
        when = when or self.clock.today()
        if when > self.clock.today():
            raise ValidationFailed("A data da alocação não pode ser futura.", field_errors={"allocation_date": "Data futura"})
        allocation = GoalAllocation(
            user_id=user_id, goal_id=goal.id, amount_cents=amount_cents, allocation_date=when,
            note=clean_text(note, max_len=200) or None,
        )
        self.db.add(allocation)
        self.db.flush()
        self._refresh_status(goal)
        self.audit.record(
            "goal.allocated", user_id=user_id, entity_type="goal", entity_id=goal.id,
            changes={"amount_cents": amount_cents, "allocation_date": when.isoformat(), "reserva_virtual": True},
        )
        self.db.commit()
        return allocation

    def reverse_allocation(self, user_id: uuid.UUID, goal_id: str, allocation_id: str) -> GoalAllocation:
        """Reversão cria uma linha negativa vinculada à original (histórico preservado)."""
        goal = self.get(user_id, goal_id, for_update=True)
        original = get_owned(self.db, GoalAllocation, allocation_id, user_id, message="Alocação não encontrada.")
        if original.goal_id != goal.id or original.amount_cents <= 0 or original.reverses_id is not None:
            raise BusinessRuleViolation("Esta alocação não pode ser revertida.", code="invalid_reversal")
        already = self.db.scalar(select(GoalAllocation.id).where(GoalAllocation.reverses_id == original.id))
        if already:
            raise BusinessRuleViolation("Esta alocação já foi revertida.", code="already_reversed")
        reversal = GoalAllocation(
            user_id=user_id, goal_id=goal.id, amount_cents=-original.amount_cents,
            allocation_date=self.clock.today(), note="Reversão de alocação", reverses_id=original.id,
        )
        self.db.add(reversal)
        self.db.flush()
        self._refresh_status(goal)
        self.audit.record(
            "goal.allocation_reversed", user_id=user_id, entity_type="goal", entity_id=goal.id,
            changes={"allocation_id": str(original.id), "amount_cents": original.amount_cents},
        )
        self.db.commit()
        return reversal

    def is_reversed(self, allocation_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        if not allocation_ids:
            return set()
        return set(
            self.db.scalars(select(GoalAllocation.reverses_id).where(GoalAllocation.reverses_id.in_(allocation_ids)))
        )

    def _refresh_status(self, goal: Goal) -> None:
        """RN043: status acompanha o progresso recalculado."""
        if goal.status is GoalStatus.ARCHIVED:
            return
        allocated = self._allocated([goal.id]).get(goal.id, 0)
        goal.status = GoalStatus.COMPLETED if allocated >= goal.target_cents else GoalStatus.ACTIVE

