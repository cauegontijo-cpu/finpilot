"""Agregações do dashboard, orçamentos (RN029–RN037) e previsão de caixa (RN071–RN077)."""

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ConflictError
from app.domain.dates import Month
from app.domain.ledger import TransactionStatus, TransactionType
from app.models import CategoryKind
from app.services.analytics import AnalyticsService, Scope
from app.services.budgets import BudgetService
from app.services.forecast import ForecastService
from app.services.transactions import TransactionService
from tests.factories import audit, category, make_account, make_transfer, make_tx

OCT = Month(2026, 10)


def budgets(db: Session) -> BudgetService:
    return BudgetService(db, Clock(), audit(db))


def test_monthly_competence_excludes_transfers_and_separates_planned(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=500_000)
    b = make_account(db, user, name="B", initial=0)
    make_tx(db, user, a, 300_000, tx_type=TransactionType.INCOME, when=date(2026, 10, 5))
    make_tx(db, user, a, 50_000, when=date(2026, 10, 6))
    make_tx(db, user, a, 20_000, when=date(2026, 10, 25), status=TransactionStatus.PLANNED)
    make_transfer(db, user, a, b, 100_000, when=date(2026, 10, 7))
    make_tx(db, user, a, 7_000, when=date(2026, 9, 6))
    months = AnalyticsService(db).monthly_competence(user.id, Month(2026, 9), OCT)
    sep, octo = months
    assert (octo.income, octo.expense, octo.expense_planned) == (300_000, 50_000, 20_000)
    assert sep.expense == 7_000
    assert octo.net == 250_000
    assert octo.savings_rate == pytest.approx(83.3)


def test_category_breakdown_and_account_scope(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=100_000)
    b = make_account(db, user, name="B", initial=100_000)
    make_tx(db, user, a, 1_000, category_name="Alimentação")
    make_tx(db, user, b, 3_000, category_name="Transporte")
    service = AnalyticsService(db)
    all_cats = service.expenses_by_category(user.id, OCT)
    assert [(c.name, c.settled) for c in all_cats] == [("Transporte", 3_000), ("Alimentação", 1_000)]
    only_a = service.expenses_by_category(user.id, OCT, Scope(account_id=a.id))
    assert [(c.name, c.settled) for c in only_a] == [("Alimentação", 1_000)]


def test_balance_evolution_matches_current_balance(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=100_000, start=date(2026, 8, 1))
    b = make_account(db, user, name="B", initial=50_000, start=date(2026, 9, 15))
    make_tx(db, user, a, 20_000, tx_type=TransactionType.INCOME, when=date(2026, 8, 10))
    make_tx(db, user, a, 5_000, when=date(2026, 9, 10))
    make_transfer(db, user, a, b, 10_000, when=date(2026, 10, 1))
    series = dict(AnalyticsService(db).balance_evolution(user.id, Month(2026, 7), OCT))
    assert series[Month(2026, 7)] == 0
    assert series[Month(2026, 8)] == 120_000
    assert series[Month(2026, 9)] == 165_000  # conta B entra com saldo inicial
    assert series[OCT] == 165_000  # transferência não altera o consolidado
    assert AnalyticsService(db).current_balance(user.id) == 165_000


def test_cash_flow_excludes_transfers_in_consolidated(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=100_000)
    b = make_account(db, user, name="B", initial=0)
    make_tx(db, user, a, 9_000, tx_type=TransactionType.INCOME)
    make_tx(db, user, a, 4_000)
    make_transfer(db, user, a, b, 1_000)
    service = AnalyticsService(db)
    flow = service.cash_flow(user.id, OCT.first_day, OCT.last_day)
    assert (flow.inflow, flow.outflow) == (9_000, 4_000)
    flow_b = service.cash_flow(user.id, OCT.first_day, OCT.last_day, Scope(account_id=b.id))
    assert flow_b.inflow == 1_000


# ---------------------------------------------------------------- orçamentos
def test_budget_usage_levels_and_planned_separation(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=1_000_000)
    food = category(db, user, "Alimentação")
    transport = category(db, user, "Transporte")
    leisure = category(db, user, "Lazer")
    service = budgets(db)
    service.create(user.id, food.id, OCT, 120_000)
    service.create(user.id, transport.id, OCT, 50_000)
    service.create(user.id, leisure.id, OCT, 40_000)
    make_tx(db, user, account, 96_000, category_name="Alimentação")  # 80%
    make_tx(db, user, account, 10_000, category_name="Transporte")
    make_tx(db, user, account, 30_000, category_name="Transporte", status=TransactionStatus.PLANNED, when=date(2026, 10, 30))
    make_tx(db, user, account, 41_000, category_name="Lazer")  # 102,5%
    make_tx(db, user, account, 99_000, category_name="Alimentação", when=date(2026, 9, 10))  # outro mês
    overview = service.overview(user.id, OCT)
    by_name = {i.budget.category.name: i for i in overview.items}
    assert by_name["Alimentação"].level == "warning"  # RN034
    assert by_name["Lazer"].level == "over"  # RN035
    assert by_name["Transporte"].level == "ok"
    assert (by_name["Transporte"].used, by_name["Transporte"].planned) == (10_000, 30_000)  # RN033
    assert by_name["Alimentação"].previous_used == 99_000
    assert overview.total_used == 147_000


def test_budget_ignores_transfers_and_recalculates_after_cancel(db: Session, user: Any) -> None:
    a = make_account(db, user, name="A", initial=100_000)
    b = make_account(db, user, name="B", initial=0)
    food = category(db, user, "Alimentação")
    service = budgets(db)
    service.create(user.id, food.id, OCT, 10_000)
    make_transfer(db, user, a, b, 9_000)  # RN032
    tx = make_tx(db, user, a, 8_500, category_name="Alimentação")
    assert service.overview(user.id, OCT).items[0].used == 8_500
    TransactionService(db, Clock(), audit(db)).cancel(user.id, str(tx.id))
    assert service.overview(user.id, OCT).items[0].used == 0  # RN036


def test_duplicate_budget_same_category_month_rejected(db: Session, user: Any) -> None:
    food = category(db, user, "Alimentação")
    service = budgets(db)
    service.create(user.id, food.id, OCT, 10_000)
    with pytest.raises(ConflictError):
        service.create(user.id, food.id, OCT, 20_000)  # RN030
    service.create(user.id, food.id, OCT.shift(1), 20_000)


def test_budget_requires_expense_category(db: Session, user: Any) -> None:
    salary = category(db, user, "Salário", CategoryKind.INCOME)
    with pytest.raises(BusinessRuleViolation):
        budgets(db).create(user.id, salary.id, OCT, 10_000)


def test_copy_budgets_from_previous_month(db: Session, user: Any) -> None:
    service = budgets(db)
    service.create(user.id, category(db, user, "Alimentação").id, Month(2026, 9), 10_000)
    service.create(user.id, category(db, user, "Lazer").id, Month(2026, 9), 5_000)
    assert service.copy_from_previous(user.id, OCT) == 2
    assert service.copy_from_previous(user.id, OCT) == 0


# ---------------------------------------------------------------- previsão
def test_forecast_starts_from_realized_balance_and_finds_negative_day(db: Session, user: Any) -> None:
    today = date(2026, 10, 15)
    a = make_account(db, user, name="A", initial=100_000)
    b = make_account(db, user, name="B", initial=0)
    make_tx(db, user, a, 80_000, when=date(2026, 10, 20), status=TransactionStatus.PLANNED)
    make_tx(db, user, a, 50_000, when=date(2026, 10, 21), status=TransactionStatus.PLANNED, due=date(2026, 10, 25))
    make_tx(db, user, a, 30_000, tx_type=TransactionType.INCOME, when=date(2026, 11, 5), status=TransactionStatus.PLANNED)
    make_tx(db, user, a, 1_000, when=date(2026, 10, 1), status=TransactionStatus.PLANNED)  # atrasada
    make_transfer(db, user, a, b, 5_000, when=date(2026, 10, 22), status=TransactionStatus.PLANNED)
    f = ForecastService(db).build(user.id, today, 30)
    assert f.starting_balance == 100_000  # RN071
    assert f.inflow == 30_000 and f.outflow == 131_000  # RN072; transferência ignorada (RN074)
    first = f.first_negative
    assert first is not None and first[0] == date(2026, 10, 25)  # RN073: data do vencimento
    assert f.ending_balance == -1_000
    assert any("atraso" in w for w in f.warnings)
    f_account_b = ForecastService(db).build(user.id, today, 30, b.id)
    assert f_account_b.inflow == 5_000  # numa conta específica, a transferência é entrada


def test_forecast_without_events_reports_uncertainty(db: Session, user: Any) -> None:
    make_account(db, user, initial=10_000)
    f = ForecastService(db).build(user.id, date(2026, 10, 15), 60)
    assert f.events == [] and f.ending_balance == 10_000
    assert any("não é uma garantia" in w for w in f.warnings)  # RN076
    assert len(f.series) == 61


# ---------------------------------------------------------------- telas
def test_dashboard_renders_with_data_and_charts(auth_client: TestClient, user: Any, db: Session) -> None:
    account = make_account(db, user, initial=200_000)
    make_tx(db, user, account, 500_000, tx_type=TransactionType.INCOME, when=date(2026, 10, 5))
    make_tx(db, user, account, 120_000, when=date(2026, 10, 8))
    make_tx(db, user, account, 30_000, when=Clock().today() + timedelta(days=10), status=TransactionStatus.PLANNED)
    budgets(db).create(user.id, category(db, user, "Alimentação").id, Month.of(Clock().today()), 100_000)
    response = auth_client.get(f"/app?month={Month.of(Clock().today()).iso()}")
    assert response.status_code == 200
    body = response.text
    for chart_id in ["ch-income-expense-data", "ch-categories-data", "ch-balance-data", "ch-budgets-data", "ch-forecast-data"]:
        assert chart_id in body
    assert "Competência" in body and "Caixa" in body and "Projetado" in body
    assert 'type="application/json"' in body
    partial = auth_client.get("/app?month=2026-10", headers={"HX-Request": "true", "HX-Target": "dashboard-content"})
    assert partial.status_code == 200 and "<html" not in partial.text


def test_dashboard_empty_state(auth_client: TestClient) -> None:
    body = auth_client.get("/app").text
    assert "Seu painel ainda está vazio" in body


def test_dashboard_ignores_other_users_account_filter(auth_client: TestClient, db: Session, other_user: Any, user: Any) -> None:
    theirs = make_account(db, other_user, name="Do Bruno", initial=999_999)
    mine = make_account(db, user, initial=1_000)
    del mine
    body = auth_client.get(f"/app?account_id={theirs.id}").text
    assert "R$ 9.999,99" not in body


def test_forecast_and_budget_pages_render(auth_client: TestClient, user: Any, db: Session) -> None:
    make_account(db, user, initial=10_000)
    assert auth_client.get("/app/previsao?dias=90").status_code == 200
    assert auth_client.get("/app/previsao?dias=7").status_code == 200  # horizonte inválido → padrão
    assert auth_client.get("/app/orcamentos?month=2026-10").status_code == 200
    assert auth_client.get("/app/orcamentos/novo?month=2026-10").status_code == 200

