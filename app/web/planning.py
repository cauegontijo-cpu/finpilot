"""Telas de metas e recorrências."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.deps import Svc, VerifiedSession, financial_rate_limit
from app.core.errors import AppError
from app.domain.ledger import TransactionType
from app.domain.money import format_input_brl
from app.domain.recurrence import FREQUENCY_LABELS, Frequency
from app.models import CategoryKind, Goal, RecurrenceRule
from app.models.planning import GOAL_STATUS_LABELS, RECURRENCE_STATUS_LABELS
from app.services.goals import GoalInput
from app.services.recurrences import RecurrenceInput
from app.utils.validation import PALETTE, SELECTABLE_ICONS
from app.web.common import failure, form_values, form_view, success
from app.web.forms import FormReader
from app.web.render import page

router = APIRouter(prefix="/app", include_in_schema=False)
write_guard = [Depends(financial_rate_limit)]
GOAL_FORM = "partials/goals/form.html"
ALLOC_FORM = "partials/goals/allocate.html"
REC_FORM = "partials/recurrences/form.html"


# ================================================================ metas
def _goal_ctx(
    goal: Goal | None, values: dict[str, Any], errors: dict[str, str], error: str | None = None
) -> dict[str, Any]:
    return {
        "goal": goal,
        "values": values,
        "errors": errors,
        "error": error,
        "palette": PALETTE,
        "icons": SELECTABLE_ICONS,
    }


def _read_goal(form: Any) -> GoalInput:
    reader = FormReader(form)
    data = GoalInput(
        name=reader.text("name"),
        target_cents=reader.money("target"),
        deadline=reader.date("deadline", required=False),
        description=reader.text("description") or None,
        color=reader.text("color", "#7c3aed"),
        icon=reader.text("icon", "target"),
    )
    reader.raise_if_errors()
    return data


@router.get("/metas")
def goals_index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    show_archived = request.query_params.get("arquivadas") == "1"
    items = svc.goals.list(session.user.id, include_archived=show_archived)
    return page(
        request,
        "pages/goals/index.html",
        {
            "items": items,
            "summary": svc.goals.reservation_summary(session.user.id),
            "labels": GOAL_STATUS_LABELS,
            "show_archived": show_archived,
        },
    )


@router.get("/metas/nova")
def goal_new(request: Request, session: VerifiedSession) -> Response:
    return form_view(
        request,
        GOAL_FORM,
        _goal_ctx(None, {"color": "#7c3aed", "icon": "target"}, {}),
        title="Nova meta",
    )


@router.post("/metas", dependencies=write_guard)
async def goal_create(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    try:
        svc.goals.create(session.user.id, _read_goal(form))
    except AppError as exc:
        return form_view(
            request,
            GOAL_FORM,
            _goal_ctx(None, form_values(form), exc.field_errors, exc.detail),
            title="Nova meta",
            status_code=422,
        )
    return success(request, "Meta criada.", fallback_url="/app/metas")


@router.get("/metas/{goal_id}")
def goal_detail(request: Request, goal_id: str, session: VerifiedSession, svc: Svc) -> Response:
    progress = svc.goals.progress(session.user.id, goal_id)
    allocations = svc.goals.allocations(session.user.id, goal_id)
    reversed_ids = svc.goals.is_reversed([a.id for a in allocations])
    history = svc.audit.list_for_user(session.user.id, entity_id=progress.goal.id, limit=30)
    return page(
        request,
        "pages/goals/detail.html",
        {
            "p": progress,
            "allocations": allocations,
            "reversed_ids": reversed_ids,
            "history": history,
            "labels": GOAL_STATUS_LABELS,
        },
    )


@router.get("/metas/{goal_id}/editar")
def goal_edit(request: Request, goal_id: str, session: VerifiedSession, svc: Svc) -> Response:
    g = svc.goals.get(session.user.id, goal_id)
    values = {
        "name": g.name,
        "target": format_input_brl(g.target_cents),
        "deadline": g.deadline.isoformat() if g.deadline else "",
        "description": g.description or "",
        "color": g.color,
        "icon": g.icon,
    }
    return form_view(request, GOAL_FORM, _goal_ctx(g, values, {}), title="Editar meta")


@router.post("/metas/{goal_id}", dependencies=write_guard)
async def goal_update(
    request: Request, goal_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    g = svc.goals.get(session.user.id, goal_id)
    form = await request.form()
    try:
        svc.goals.update(session.user.id, goal_id, _read_goal(form))
    except AppError as exc:
        return form_view(
            request,
            GOAL_FORM,
            _goal_ctx(g, form_values(form), exc.field_errors, exc.detail),
            title="Editar meta",
            status_code=422,
        )
    return success(request, "Meta atualizada.", fallback_url=f"/app/metas/{goal_id}")


@router.get("/metas/{goal_id}/alocar")
def goal_allocate_form(
    request: Request, goal_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    g = svc.goals.get(session.user.id, goal_id)
    values = {"allocation_date": svc.clock.today().isoformat()}
    return form_view(
        request,
        ALLOC_FORM,
        {
            "goal": g,
            "values": values,
            "errors": {},
            "summary": svc.goals.reservation_summary(session.user.id),
        },
        title="Reservar para a meta",
    )


@router.post("/metas/{goal_id}/alocar", dependencies=write_guard)
async def goal_allocate(
    request: Request, goal_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    g = svc.goals.get(session.user.id, goal_id)
    form = await request.form()
    reader = FormReader(form)
    try:
        amount = reader.money("amount")
        when = reader.date("allocation_date", required=False)
        reader.raise_if_errors()
        svc.goals.allocate(session.user.id, goal_id, amount, when, reader.text("note") or None)
    except AppError as exc:
        ctx = {
            "goal": g,
            "values": form_values(form),
            "errors": exc.field_errors,
            "error": exc.detail,
            "summary": svc.goals.reservation_summary(session.user.id),
        }
        return form_view(request, ALLOC_FORM, ctx, title="Reservar para a meta", status_code=422)
    summary = svc.goals.reservation_summary(session.user.id)
    message = "Reserva registrada."
    if summary.over_reserved:
        message += " Atenção: o total reservado em metas supera o saldo disponível nas contas."
    return success(request, message, fallback_url=f"/app/metas/{goal_id}")


@router.post("/metas/{goal_id}/alocacoes/{allocation_id}/reverter", dependencies=write_guard)
def goal_reverse(
    request: Request, goal_id: str, allocation_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    try:
        svc.goals.reverse_allocation(session.user.id, goal_id, allocation_id)
    except AppError as exc:
        return failure(request, exc, fallback_url=f"/app/metas/{goal_id}")
    return success(request, "Alocação revertida.", fallback_url=f"/app/metas/{goal_id}")


@router.post("/metas/{goal_id}/arquivar", dependencies=write_guard)
def goal_archive(request: Request, goal_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.goals.set_archived(session.user.id, goal_id, True)
    return success(request, "Meta arquivada.", fallback_url="/app/metas")


@router.post("/metas/{goal_id}/reativar", dependencies=write_guard)
def goal_restore(request: Request, goal_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.goals.set_archived(session.user.id, goal_id, False)
    return success(request, "Meta reativada.", fallback_url=f"/app/metas/{goal_id}")


# ================================================================ recorrências
def _rec_ctx(
    svc: Svc,
    user_id: Any,
    rule: RecurrenceRule | None,
    values: dict[str, Any],
    errors: dict[str, str],
    error: str | None = None,
) -> dict[str, Any]:
    categories = svc.categories.list(user_id)
    return {
        "rule": rule,
        "values": values,
        "errors": errors,
        "error": error,
        "account_options": [(str(a.id), a.name) for a in svc.accounts.list(user_id)],
        "expense_categories": [c for c in categories if c.kind is CategoryKind.EXPENSE],
        "income_categories": [c for c in categories if c.kind is CategoryKind.INCOME],
        "frequencies": [(f.value, label) for f, label in FREQUENCY_LABELS.items()],
    }


def _read_rec(form: Any, today: Any, forced_type: TransactionType | None = None) -> RecurrenceInput:
    reader = FormReader(form)
    tx_type = forced_type or reader.enum("type", TransactionType, TransactionType.EXPENSE)
    category_field = (
        "income_category_id" if tx_type is TransactionType.INCOME else "expense_category_id"
    )
    data = RecurrenceInput(
        type=tx_type,
        account_id=reader.raw("account_id"),
        category_id=reader.raw(category_field),
        description=reader.text("description"),
        amount_cents=reader.money("amount"),
        frequency=reader.enum("frequency", Frequency, Frequency.MONTHLY),
        start_date=reader.required_date("start_date", today),
        interval=reader.integer("interval", 1, min_value=1, max_value=365),
        end_date=reader.date("end_date", required=False),
        auto_settle=reader.bool("auto_settle"),
        notes=reader.text("notes") or None,
    )
    reader.raise_if_errors()
    return data


@router.get("/recorrencias")
def recurrences_index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    rules = svc.recurrences.list(session.user.id)
    upcoming = {r.id: svc.recurrences.next_occurrences(r, 3) for r in rules}
    return page(
        request,
        "pages/recurrences/index.html",
        {
            "rules": rules,
            "upcoming": upcoming,
            "freq_labels": FREQUENCY_LABELS,
            "status_labels": RECURRENCE_STATUS_LABELS,
        },
    )


@router.get("/recorrencias/nova")
def recurrence_new(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    values = {
        "type": "expense",
        "frequency": "monthly",
        "interval": "1",
        "start_date": svc.clock.today().isoformat(),
    }
    return form_view(
        request,
        REC_FORM,
        _rec_ctx(svc, session.user.id, None, values, {}),
        title="Nova recorrência",
    )


@router.post("/recorrencias", dependencies=write_guard)
async def recurrence_create(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    try:
        svc.recurrences.create(session.user.id, _read_rec(form, svc.clock.today()))
    except AppError as exc:
        errors = dict(exc.field_errors)
        if "category_id" in errors:
            errors["expense_category_id"] = errors["income_category_id"] = errors.pop("category_id")
        return form_view(
            request,
            REC_FORM,
            _rec_ctx(svc, session.user.id, None, form_values(form), errors, exc.detail),
            title="Nova recorrência",
            status_code=422,
        )
    return success(
        request, "Recorrência criada e ocorrências geradas.", fallback_url="/app/recorrencias"
    )


@router.get("/recorrencias/{rule_id}")
def recurrence_detail(
    request: Request, rule_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    rule = svc.recurrences.get(session.user.id, rule_id)
    return page(
        request,
        "pages/recurrences/detail.html",
        {
            "rule": rule,
            "next": svc.recurrences.next_occurrences(rule, 8),
            "occurrences": svc.recurrences.occurrences(session.user.id, rule_id),
            "freq_labels": FREQUENCY_LABELS,
            "status_labels": RECURRENCE_STATUS_LABELS,
        },
    )


@router.get("/recorrencias/{rule_id}/editar")
def recurrence_edit(request: Request, rule_id: str, session: VerifiedSession, svc: Svc) -> Response:
    r = svc.recurrences.get(session.user.id, rule_id)
    cat_key = "income_category_id" if r.type is TransactionType.INCOME else "expense_category_id"
    values = {
        "type": r.type.value,
        "description": r.description,
        "amount": format_input_brl(r.amount_cents),
        "account_id": str(r.account_id),
        cat_key: str(r.category_id),
        "frequency": r.frequency.value,
        "interval": str(r.interval),
        "start_date": r.start_date.isoformat(),
        "end_date": r.end_date.isoformat() if r.end_date else "",
        "auto_settle": "true" if r.auto_settle else "",
        "notes": r.notes or "",
    }
    return form_view(
        request, REC_FORM, _rec_ctx(svc, session.user.id, r, values, {}), title="Editar recorrência"
    )


@router.post("/recorrencias/{rule_id}", dependencies=write_guard)
async def recurrence_update(
    request: Request, rule_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    rule = svc.recurrences.get(session.user.id, rule_id)
    form = await request.form()
    try:
        svc.recurrences.update(
            session.user.id, rule_id, _read_rec(form, svc.clock.today(), forced_type=rule.type)
        )
    except AppError as exc:
        values = {**form_values(form), "type": rule.type.value}
        return form_view(
            request,
            REC_FORM,
            _rec_ctx(svc, session.user.id, rule, values, exc.field_errors, exc.detail),
            title="Editar recorrência",
            status_code=422,
        )
    return success(
        request,
        "Recorrência atualizada. Somente ocorrências futuras previstas foram ajustadas.",
        fallback_url=f"/app/recorrencias/{rule_id}",
    )


@router.post("/recorrencias/{rule_id}/{action}", dependencies=write_guard)
def recurrence_action(
    request: Request, rule_id: str, action: str, session: VerifiedSession, svc: Svc
) -> Response:
    handlers = {
        "pausar": (svc.recurrences.pause, "Recorrência pausada."),
        "retomar": (svc.recurrences.resume, "Recorrência retomada."),
        "cancelar": (svc.recurrences.cancel, "Recorrência cancelada. O histórico foi preservado."),
    }
    if action not in handlers:
        return failure(request, AppError("Ação inválida."), fallback_url="/app/recorrencias")
    handler, message = handlers[action]
    try:
        handler(session.user.id, rule_id)
    except AppError as exc:
        return failure(request, exc, fallback_url=f"/app/recorrencias/{rule_id}")
    return success(request, message, fallback_url=f"/app/recorrencias/{rule_id}")
