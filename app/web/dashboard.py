"""Dashboard financeiro e previsão de fluxo de caixa."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.deps import Svc, VerifiedSession
from app.domain.dates import Month
from app.domain.money import format_brl
from app.models import CategoryKind
from app.repositories.base import parse_uuid
from app.services.analytics import Scope
from app.services.forecast import HORIZONS
from app.web.render import is_htmx, page

router = APIRouter(prefix="/app", include_in_schema=False)


def _month(request: Request, today_month: Month) -> Month:
    q = request.query_params
    try:
        if q.get("year") and q.get("m"):
            return Month(int(q["year"]), int(q["m"]))
        if q.get("month"):
            return Month.parse(q["month"])
    except (ValueError, TypeError):
        pass
    return today_month


@router.get("")
def dashboard(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    user_id = session.user.id
    month = _month(request, Month.of(svc.clock.today()))
    accounts = svc.accounts.list(user_id)
    categories = svc.categories.list(user_id, kind=CategoryKind.EXPENSE)
    account_id = parse_uuid(request.query_params.get("account_id"))
    category_id = parse_uuid(request.query_params.get("category_id"))
    # Filtros só aceitam recursos do próprio usuário.
    if account_id and account_id not in {a.id for a in accounts}:
        account_id = None
    if category_id and category_id not in {c.id for c in categories}:
        category_id = None
    data = svc.dashboard.build(
        user_id, month, Scope(account_id=account_id, category_id=category_id)
    )
    context = {
        "d": data,
        "accounts": accounts,
        "categories": categories,
        "selected_account": str(account_id or ""),
        "selected_category": str(category_id or ""),
        "years": list(range(month.year - 4, month.year + 2)),
        "today": svc.clock.today(),
    }
    if is_htmx(request) and request.headers.get("hx-target") == "dashboard-content":
        return page(request, "partials/dashboard/content.html", context)
    return page(request, "pages/dashboard.html", context)


@router.get("/previsao")
def forecast(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    user_id = session.user.id
    try:
        days = int(request.query_params.get("dias", "30"))
    except ValueError:
        days = 30
    if days not in HORIZONS:
        days = 30
    accounts = svc.accounts.list(user_id)
    account_id = parse_uuid(request.query_params.get("account_id"))
    if account_id and account_id not in {a.id for a in accounts}:
        account_id = None
    result = svc.forecast.build(user_id, svc.clock.today(), days, account_id)
    step = 1 if days <= 30 else 2 if days <= 60 else 3
    points = result.series[::step]
    if result.series and points[-1] != result.series[-1]:
        points.append(result.series[-1])
    chart = {
        "type": "line",
        "legend": False,
        "labels": [d.strftime("%d/%m") for d, _ in points],
        "datasets": [
            {
                "label": "Saldo projetado",
                "data": [b for _, b in points],
                "color": "primary",
                "pointRadius": 0,
            }
        ],
    }
    table = {
        "headers": ["Data", "Saldo projetado"],
        "rows": [[d.strftime("%d/%m/%Y"), format_brl(b)] for d, b in points],
    }
    context = {
        "f": result,
        "days": days,
        "horizons": HORIZONS,
        "chart_spec": chart,
        "table": table,
        "accounts": accounts,
        "selected_account": str(account_id or ""),
    }
    return page(request, "pages/forecast.html", context)
