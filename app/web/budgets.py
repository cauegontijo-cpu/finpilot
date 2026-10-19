"""Telas de orçamentos mensais."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.deps import Svc, VerifiedSession, financial_rate_limit
from app.core.errors import AppError
from app.domain.dates import Month
from app.domain.money import format_input_brl
from app.models import Budget, CategoryKind
from app.web.common import failure, form_values, form_view, success
from app.web.forms import FormReader, month_param
from app.web.render import page

router = APIRouter(prefix="/app/orcamentos", include_in_schema=False)
write_guard = [Depends(financial_rate_limit)]
PARTIAL = "partials/budgets/form.html"


def _ctx(svc: Svc, user_id: Any, month: Month, budget: Budget | None, values: dict[str, Any], errors: dict[str, str], error: str | None = None) -> dict[str, Any]:
    return {
        "month": month,
        "budget": budget,
        "values": values,
        "errors": errors,
        "error": error,
        "categories": svc.categories.list(user_id, kind=CategoryKind.EXPENSE),
    }


@router.get("")
def index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    month = month_param(request.query_params.get("month"), svc.clock.today())
    overview = svc.budgets.overview(session.user.id, month)
    previous = svc.budgets.overview(session.user.id, month.shift(-1))
    return page(
        request,
        "pages/budgets/index.html",
        {"o": overview, "month": month, "prev": month.shift(-1), "next": month.shift(1), "previous_count": len(previous.items)},
    )


@router.get("/novo")
def new(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    month = month_param(request.query_params.get("month"), svc.clock.today())
    return form_view(request, PARTIAL, _ctx(svc, session.user.id, month, None, {"month": month.iso()}, {}), title="Novo orçamento")


@router.post("", dependencies=write_guard)
async def create(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    reader = FormReader(form)
    month = month_param(reader.raw("month"), svc.clock.today())
    try:
        limit = reader.money("limit")
        reader.raise_if_errors()
        svc.budgets.create(session.user.id, reader.raw("category_id") or None, month, limit)
    except AppError as exc:
        ctx = _ctx(svc, session.user.id, month, None, form_values(form), exc.field_errors, exc.detail)
        return form_view(request, PARTIAL, ctx, title="Novo orçamento", status_code=422)
    return success(request, "Orçamento criado.", fallback_url=f"/app/orcamentos?month={month.iso()}")


@router.get("/{budget_id}/editar")
def edit(request: Request, budget_id: str, session: VerifiedSession, svc: Svc) -> Response:
    budget = svc.budgets.get(session.user.id, budget_id)
    month = Month(budget.year, budget.month)
    values = {"limit": format_input_brl(budget.limit_cents), "category_id": str(budget.category_id), "month": month.iso()}
    return form_view(request, PARTIAL, _ctx(svc, session.user.id, month, budget, values, {}), title="Editar orçamento")


@router.post("/{budget_id}", dependencies=write_guard)
async def update(request: Request, budget_id: str, session: VerifiedSession, svc: Svc) -> Response:
    budget = svc.budgets.get(session.user.id, budget_id)
    month = Month(budget.year, budget.month)
    form = await request.form()
    reader = FormReader(form)
    try:
        limit = reader.money("limit")
        reader.raise_if_errors()
        svc.budgets.update(session.user.id, budget_id, limit)
    except AppError as exc:
        ctx = _ctx(svc, session.user.id, month, budget, form_values(form), exc.field_errors, exc.detail)
        return form_view(request, PARTIAL, ctx, title="Editar orçamento", status_code=422)
    return success(request, "Orçamento atualizado.", fallback_url=f"/app/orcamentos?month={month.iso()}")


@router.post("/{budget_id}/remover", dependencies=write_guard)
def archive(request: Request, budget_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.budgets.archive(session.user.id, budget_id)
    return success(request, "Orçamento removido.", fallback_url="/app/orcamentos")


@router.post("/acoes/copiar", dependencies=write_guard)
async def copy_previous(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    month = month_param(FormReader(form).raw("month"), svc.clock.today())
    try:
        created = svc.budgets.copy_from_previous(session.user.id, month)
    except AppError as exc:
        return failure(request, exc, fallback_url=f"/app/orcamentos?month={month.iso()}")
    message = f"{created} orçamento(s) copiado(s) do mês anterior." if created else "Nenhum orçamento novo para copiar."
    return success(request, message, fallback_url=f"/app/orcamentos?month={month.iso()}")
