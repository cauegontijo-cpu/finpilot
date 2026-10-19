"""Telas de transações (lançamentos) e transferências."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.deps import Svc, VerifiedSession, financial_rate_limit
from app.core.errors import AppError
from app.domain.dates import Month
from app.domain.ledger import TransactionStatus, TransactionType
from app.domain.money import format_input_brl
from app.models import CategoryKind, Transaction
from app.repositories.base import parse_uuid
from app.services.transactions import TransactionFilters, TransactionInput
from app.services.transfers import TransferInput
from app.web.common import failure, form_values, form_view, success
from app.web.forms import FormReader, month_param
from app.web.render import is_htmx, page

router = APIRouter(prefix="/app", include_in_schema=False)
write_guard = [Depends(financial_rate_limit)]
TX_FORM = "partials/transactions/form.html"
TRANSFER_FORM = "partials/transfers/form.html"


# ---------------------------------------------------------------- filtros
def _filters(request: Request, today: date) -> tuple[TransactionFilters, dict[str, str], Month | None]:
    q = request.query_params
    reader_errors: dict[str, str] = {}
    start = end = None
    month: Month | None = None
    if q.get("start") or q.get("end"):
        try:
            start = date.fromisoformat(q["start"]) if q.get("start") else None
            end = date.fromisoformat(q["end"]) if q.get("end") else None
        except ValueError:
            reader_errors["period"] = "Período inválido"
    if not start and not end and q.get("period") != "all":
        month = month_param(q.get("month"), today)
        start, end = month.first_day, month.last_day
    status = q.get("status") or None
    kind = q.get("kind") or None
    try:
        page_number = max(1, int(q.get("page", "1")))
    except ValueError:
        page_number = 1
    filters = TransactionFilters(
        start=start,
        end=end,
        account_id=parse_uuid(q.get("account_id")),
        category_id=parse_uuid(q.get("category_id")),
        status=TransactionStatus(status) if status in TransactionStatus._value2member_map_ else None,
        kind=kind if kind in ("income", "expense", "transfer", "card_payment") else None,
        q=(q.get("q") or "")[:100] or None,
        sort=q.get("sort") if q.get("sort") in _SORT_OPTIONS else None,
        page=page_number,
    )
    raw = {k: v for k, v in q.items() if k != "page" and v}
    return filters, raw, month


_SORT_OPTIONS = {
    "-competence_date": "Data (mais recentes)",
    "competence_date": "Data (mais antigas)",
    "-amount": "Maior valor",
    "amount": "Menor valor",
    "description": "Descrição (A–Z)",
}


@router.get("/transacoes")
def index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    user_id = session.user.id
    today = svc.clock.today()
    filters, raw, month = _filters(request, today)
    result = svc.transactions.list(user_id, filters)
    totals = svc.transactions.totals(user_id, filters)
    context: dict[str, Any] = {
        "page_obj": result,
        "totals": totals,
        "filters": filters,
        "raw": raw,
        "month": month,
        "prev_month": month.shift(-1).iso() if month else None,
        "next_month": month.shift(1).iso() if month else None,
        "base_query": urlencode(raw),
        "accounts": svc.accounts.list(user_id, include_archived=True),
        "categories": svc.categories.list(user_id, include_archived=True),
        "sort_options": _SORT_OPTIONS,
        "today": today,
    }
    if is_htmx(request) and request.headers.get("hx-target") == "tx-list":
        return page(request, "partials/transactions/list.html", context)
    return page(request, "pages/transactions/index.html", context)


# ---------------------------------------------------------------- formulário
def _tx_ctx(svc: Svc, user_id: Any, tx: Transaction | None, values: dict[str, Any], errors: dict[str, str], error: str | None = None) -> dict[str, Any]:
    categories = svc.categories.list(user_id)
    return {
        "tx": tx,
        "values": values,
        "errors": errors,
        "error": error,
        "accounts": svc.accounts.list(user_id),
        "expense_categories": [c for c in categories if c.kind is CategoryKind.EXPENSE],
        "income_categories": [c for c in categories if c.kind is CategoryKind.INCOME],
    }


def _read_tx(form: Any, today: date, forced_type: TransactionType | None = None) -> TransactionInput:
    reader = FormReader(form)
    tx_type = forced_type or reader.enum("type", TransactionType, TransactionType.EXPENSE)
    status = reader.enum("status", TransactionStatus, TransactionStatus.SETTLED)
    category_field = "income_category_id" if tx_type is TransactionType.INCOME else "expense_category_id"
    competence = reader.required_date("competence_date", today)
    data = TransactionInput(
        type=tx_type,
        account_id=reader.raw("account_id"),
        category_id=reader.raw(category_field) or None,
        description=reader.text("description"),
        amount_cents=reader.money("amount"),
        competence_date=competence,
        status=status,
        settlement_date=reader.date("settlement_date", required=False) if status is TransactionStatus.SETTLED else None,
        due_date=reader.date("due_date", required=False),
        notes=reader.text("notes") or None,
    )
    reader.raise_if_errors()
    return data


def _service_errors(exc: AppError) -> dict[str, str]:
    errors = dict(exc.field_errors)
    if "category_id" in errors:
        message = errors.pop("category_id")
        errors["expense_category_id"] = errors["income_category_id"] = message
    return errors


@router.get("/transacoes/nova")
def new(request: Request, session: VerifiedSession, svc: Svc, tipo: str = "expense") -> Response:
    today = svc.clock.today().isoformat()
    values = {
        "type": "income" if tipo == "income" else "expense",
        "status": "settled",
        "competence_date": today,
        "settlement_date": today,
        "account_id": request.query_params.get("account_id", ""),
    }
    return form_view(request, TX_FORM, _tx_ctx(svc, session.user.id, None, values, {}), title="Novo lançamento")


@router.post("/transacoes", dependencies=write_guard)
async def create(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    try:
        data = _read_tx(form, svc.clock.today())
        svc.transactions.create(session.user.id, data, idempotency_key=FormReader(form).raw("idempotency_key"))
    except AppError as exc:
        ctx = _tx_ctx(svc, session.user.id, None, form_values(form), _service_errors(exc), exc.detail)
        return form_view(request, TX_FORM, ctx, title="Novo lançamento", status_code=422)
    label = "Receita registrada." if data.type is TransactionType.INCOME else "Despesa registrada."
    return success(request, label, fallback_url="/app/transacoes")


@router.get("/transacoes/{tx_id}/editar")
def edit(request: Request, tx_id: str, session: VerifiedSession, svc: Svc) -> Response:
    tx = svc.transactions.get(session.user.id, tx_id)
    category_key = "income_category_id" if tx.type is TransactionType.INCOME else "expense_category_id"
    values = {
        "type": tx.type.value,
        "status": tx.status.value,
        "description": tx.description,
        "amount": format_input_brl(tx.amount_cents),
        "account_id": str(tx.account_id),
        category_key: str(tx.category_id),
        "competence_date": tx.competence_date.isoformat(),
        "settlement_date": tx.settlement_date.isoformat() if tx.settlement_date else "",
        "due_date": tx.due_date.isoformat() if tx.due_date else "",
        "notes": tx.notes or "",
    }
    return form_view(request, TX_FORM, _tx_ctx(svc, session.user.id, tx, values, {}), title="Editar lançamento")


@router.post("/transacoes/{tx_id}", dependencies=write_guard)
async def update(request: Request, tx_id: str, session: VerifiedSession, svc: Svc) -> Response:
    tx = svc.transactions.get(session.user.id, tx_id)
    form = await request.form()
    try:
        data = _read_tx(form, svc.clock.today(), forced_type=tx.type)
        svc.transactions.update(session.user.id, tx_id, data)
    except AppError as exc:
        values = {**form_values(form), "type": tx.type.value}
        ctx = _tx_ctx(svc, session.user.id, tx, values, _service_errors(exc), exc.detail)
        return form_view(request, TX_FORM, ctx, title="Editar lançamento", status_code=422)
    return success(request, "Lançamento atualizado.", fallback_url="/app/transacoes")


@router.post("/transacoes/{tx_id}/efetivar", dependencies=write_guard)
def settle(request: Request, tx_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.transactions.settle(session.user.id, tx_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/transacoes")
    return success(request, "Lançamento efetivado.", fallback_url="/app/transacoes")


@router.post("/transacoes/{tx_id}/cancelar", dependencies=write_guard)
def cancel(request: Request, tx_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        tx = svc.transactions.cancel(session.user.id, tx_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/transacoes")
    message = "Lançamento estornado e cancelado." if tx.settlement_date else "Lançamento cancelado."
    return success(request, message, fallback_url="/app/transacoes")


@router.get("/transacoes/{tx_id}/historico")
def history(request: Request, tx_id: str, session: VerifiedSession, svc: Svc) -> Response:
    tx = svc.transactions.get(session.user.id, tx_id)
    logs = svc.transactions.history(session.user.id, tx_id)
    return form_view(request, "partials/transactions/history.html", {"tx": tx, "logs": logs}, title="Histórico")


# ---------------------------------------------------------------- transferências
@router.get("/transferencias")
def transfers_index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    try:
        page_number = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page_number = 1
    result = svc.transfers.list(session.user.id, page=page_number)
    return page(request, "pages/transfers/index.html", {"page_obj": result, "base_query": ""})


def _transfer_ctx(svc: Svc, user_id: Any, values: dict[str, Any], errors: dict[str, str], error: str | None = None) -> dict[str, Any]:
    return {"values": values, "errors": errors, "error": error, "accounts": svc.accounts.list(user_id)}


@router.get("/transferencias/nova")
def new_transfer(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    values = {"transfer_date": svc.clock.today().isoformat(), "status": "settled"}
    return form_view(request, TRANSFER_FORM, _transfer_ctx(svc, session.user.id, values, {}), title="Nova transferência")


@router.post("/transferencias", dependencies=write_guard)
async def create_transfer(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    reader = FormReader(form)
    try:
        data = TransferInput(
            from_account_id=reader.raw("from_account_id"),
            to_account_id=reader.raw("to_account_id"),
            amount_cents=reader.money("amount"),
            transfer_date=reader.required_date("transfer_date", svc.clock.today()),
            description=reader.text("description") or None,
            status=reader.enum("status", TransactionStatus, TransactionStatus.SETTLED),
        )
        if not data.from_account_id or not data.to_account_id:
            reader.errors.setdefault("from_account_id" if not data.from_account_id else "to_account_id", "Selecione a conta")
        reader.raise_if_errors()
        svc.transfers.create(session.user.id, data, idempotency_key=reader.raw("idempotency_key"))
    except AppError as exc:
        ctx = _transfer_ctx(svc, session.user.id, form_values(form), exc.field_errors, exc.detail)
        return form_view(request, TRANSFER_FORM, ctx, title="Nova transferência", status_code=422)
    return success(request, "Transferência registrada.", fallback_url="/app/transferencias")


@router.post("/transferencias/{transfer_id}/efetivar", dependencies=write_guard)
def settle_transfer(request: Request, transfer_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.transfers.settle(session.user.id, transfer_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/transferencias")
    return success(request, "Transferência efetivada.", fallback_url="/app/transferencias")


@router.post("/transferencias/{transfer_id}/cancelar", dependencies=write_guard)
def cancel_transfer(request: Request, transfer_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.transfers.cancel(session.user.id, transfer_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/transferencias")
    return success(request, "Transferência cancelada e estornada nas duas contas.", fallback_url="/app/transferencias")

