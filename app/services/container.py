"""Contêiner de serviços por requisição (injeção de dependências simples e explícita)."""

from __future__ import annotations

from functools import cached_property

from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Settings
from app.services.accounts import AccountService
from app.services.analytics import AnalyticsService
from app.services.audit import AuditService, RequestMeta
from app.services.budgets import BudgetService
from app.services.categories import CategoryService
from app.services.dashboard import DashboardService
from app.services.forecast import ForecastService
from app.services.goals import GoalService
from app.services.imports import ImportService
from app.services.recurrences import RecurrenceService
from app.services.reports import ReportService
from app.services.transactions import TransactionService
from app.services.transfers import TransferService


class Services:
    def __init__(self, db: Session, clock: Clock, meta: RequestMeta, settings: Settings) -> None:
        self.db = db
        self.clock = clock
        self.meta = meta
        self.settings = settings

    @cached_property
    def audit(self) -> AuditService:
        return AuditService(self.db, self.meta)

    @cached_property
    def accounts(self) -> AccountService:
        return AccountService(self.db, self.clock, self.audit)

    @cached_property
    def categories(self) -> CategoryService:
        return CategoryService(self.db, self.clock, self.audit)

    @cached_property
    def transactions(self) -> TransactionService:
        return TransactionService(self.db, self.clock, self.audit)

    @cached_property
    def transfers(self) -> TransferService:
        return TransferService(self.db, self.clock, self.audit)

    @cached_property
    def analytics(self) -> AnalyticsService:
        return AnalyticsService(self.db)

    @cached_property
    def budgets(self) -> BudgetService:
        return BudgetService(self.db, self.clock, self.audit)

    @cached_property
    def dashboard(self) -> DashboardService:
        return DashboardService(self.db, self.clock, self.audit)

    @cached_property
    def forecast(self) -> ForecastService:
        return ForecastService(self.db)

    @cached_property
    def goals(self) -> GoalService:
        return GoalService(self.db, self.clock, self.audit)

    @cached_property
    def recurrences(self) -> RecurrenceService:
        return RecurrenceService(
            self.db, self.clock, self.audit, self.settings.recurrence_horizon_days
        )

    @cached_property
    def imports(self) -> ImportService:
        return ImportService(self.db, self.clock, self.audit, self.settings)

    @cached_property
    def reports(self) -> ReportService:
        return ReportService(self.db, self.clock, self.audit)
