"""Telas de contas financeiras."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.deps import Svc, VerifiedSession, financial_rate_limit
from app.core.errors import AppError
from app.domain.money import format_input_brl
from app.models import ACCOUNT_TYPE_LABELS, Account, AccountType
from app.services.accounts import AccountInput
from app.utils.validation import PALETTE, SELECTABLE_ICONS
from app.web.common import failure, form_values, form_view, success
from app.web.forms import FormReader
from app.web.render import page

router = APIRouter(prefix="/app/contas", include_in_schema=False)
write_guard = [Depends(financial_rate_limit)]


def _form_context(
    account: Account | None,
    values: dict[str, Any],
    errors: dict[str, str],
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "account": account,
        "values": values,
        "errors": errors,
        "error": error,
        "type_options": [(t.value, label) for t, label in ACCOUNT_TYPE_LABELS.items()],
        "palette": PALETTE,
        "icons": SELECTABLE_ICONS[:12],
    }


def _read(reader: FormReader, today: Any) -> AccountInput:
    data = AccountInput(
        name=reader.text("name"),
        type=reader.enum("type", AccountType, AccountType.CHECKING),
        initial_balance_cents=reader.money("initial_balance", positive=False),
        start_date=reader.required_date("start_date", today),
        allow_negative=reader.bool("allow_negative"),
        institution=reader.text("institution") or None,
        color=reader.text("color", "#2563eb"),
        icon=reader.text("icon", "wallet"),
    )
    reader.raise_if_errors()
    return data


@router.get("")
def index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    all_accounts = svc.accounts.list(session.user.id, include_archived=True)
    active = [a for a in all_accounts if a.is_active]
    return page(
        request,
        "pages/accounts/index.html",
        {
            "active": active,
            "archived": [a for a in all_accounts if not a.is_active],
            "total": sum(a.current_balance_cents for a in active),
            "type_labels": ACCOUNT_TYPE_LABELS,
        },
    )


@router.get("/nova")
def new(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    values = {
        "start_date": svc.clock.today().isoformat(),
        "type": "checking",
        "color": "#2563eb",
        "icon": "wallet",
    }
    return form_view(
        request, "partials/accounts/form.html", _form_context(None, values, {}), title="Nova conta"
    )


@router.post("", dependencies=write_guard)
async def create(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    try:
        data = _read(FormReader(form), svc.clock.today())
        svc.accounts.create(session.user.id, data)
    except AppError as exc:
        return form_view(
            request,
            "partials/accounts/form.html",
            _form_context(None, form_values(form), exc.field_errors, exc.detail),
            title="Nova conta",
            status_code=422,
        )
    return success(request, "Conta criada.", fallback_url="/app/contas")


@router.get("/{account_id}/editar")
def edit(request: Request, account_id: str, session: VerifiedSession, svc: Svc) -> Response:
    account = svc.accounts.get(session.user.id, account_id)
    values = {
        "name": account.name,
        "type": account.type.value,
        "institution": account.institution or "",
        "initial_balance": format_input_brl(account.initial_balance_cents),
        "start_date": account.start_date.isoformat(),
        "allow_negative": "true" if account.allow_negative else "",
        "color": account.color,
        "icon": account.icon,
    }
    return form_view(
        request,
        "partials/accounts/form.html",
        _form_context(account, values, {}),
        title="Editar conta",
    )


@router.post("/{account_id}", dependencies=write_guard)
async def update(request: Request, account_id: str, session: VerifiedSession, svc: Svc) -> Response:
    account = svc.accounts.get(session.user.id, account_id)
    form = await request.form()
    try:
        data = _read(FormReader(form), svc.clock.today())
        svc.accounts.update(session.user.id, account_id, data)
    except AppError as exc:
        return form_view(
            request,
            "partials/accounts/form.html",
            _form_context(account, form_values(form), exc.field_errors, exc.detail),
            title="Editar conta",
            status_code=422,
        )
    return success(request, "Conta atualizada.", fallback_url="/app/contas")


@router.post("/{account_id}/arquivar", dependencies=write_guard)
def archive(request: Request, account_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.accounts.set_archived(session.user.id, account_id, True)
    return success(
        request, "Conta arquivada. O histórico foi preservado.", fallback_url="/app/contas"
    )


@router.post("/{account_id}/reativar", dependencies=write_guard)
def restore(request: Request, account_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.accounts.set_archived(session.user.id, account_id, False)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/contas")
    return success(request, "Conta reativada.", fallback_url="/app/contas")


@router.post("/{account_id}/excluir", dependencies=write_guard)
def delete(request: Request, account_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.accounts.delete(session.user.id, account_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/contas")
    return success(request, "Conta excluída.", fallback_url="/app/contas")
