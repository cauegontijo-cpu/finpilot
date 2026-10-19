"""Telas de cartões de crédito, compras, faturas e pagamentos."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.deps import Svc, VerifiedSession, financial_rate_limit
from app.core.errors import AppError
from app.domain.installments import plan_purchase
from app.domain.money import format_input_brl
from app.models import CategoryKind, CreditCard
from app.services.cards import CardInput, CardService, PurchaseInput
from app.utils.validation import PALETTE
from app.web.common import failure, form_values, form_view, success
from app.web.forms import FormReader
from app.web.render import page

router = APIRouter(prefix="/app", include_in_schema=False)
write_guard = [Depends(financial_rate_limit)]
CARD_FORM = "partials/cards/form.html"
PURCHASE_FORM = "partials/cards/purchase.html"
PAY_FORM = "partials/cards/pay.html"


def cards(svc: Svc) -> CardService:
    return CardService(svc.db, svc.clock, svc.audit)


def _card_ctx(card: CreditCard | None, values: dict[str, Any], errors: dict[str, str], error: str | None = None) -> dict[str, Any]:
    return {"card": card, "values": values, "errors": errors, "error": error, "palette": PALETTE}


def _read_card(form: Any) -> CardInput:
    reader = FormReader(form)
    data = CardInput(
        name=reader.text("name"),
        limit_cents=reader.money("limit"),
        closing_day=reader.integer("closing_day", 0, min_value=1, max_value=31),
        due_day=reader.integer("due_day", 0, min_value=1, max_value=31),
        institution=reader.text("institution") or None,
        last4=reader.text("last4") or None,
        color=reader.text("color", "#0b1b3f"),
    )
    reader.raise_if_errors()
    return data


@router.get("/cartoes")
def index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    return page(request, "pages/cards/index.html", {"summaries": cards(svc).summaries(session.user.id), "today": svc.clock.today()})


@router.get("/cartoes/novo")
def new(request: Request, session: VerifiedSession) -> Response:
    return form_view(request, CARD_FORM, _card_ctx(None, {"color": "#0b1b3f"}, {}), title="Novo cartão")


@router.post("/cartoes", dependencies=write_guard)
async def create(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    try:
        cards(svc).create(session.user.id, _read_card(form))
    except AppError as exc:
        return form_view(request, CARD_FORM, _card_ctx(None, form_values(form), exc.field_errors, exc.detail), title="Novo cartão", status_code=422)
    return success(request, "Cartão cadastrado.", fallback_url="/app/cartoes")


@router.get("/cartoes/{card_id}")
def detail(request: Request, card_id: str, session: VerifiedSession, svc: Svc) -> Response:
    service = cards(svc)
    card = service.get(session.user.id, card_id)
    summary = next((s for s in service.summaries(session.user.id) if s.card.id == card.id), None)
    return page(
        request,
        "pages/cards/detail.html",
        {
            "card": card,
            "summary": summary,
            "statements": list(reversed(service.statements(session.user.id, card_id))),
            "purchases": service.purchases(session.user.id, card_id),
            "today": svc.clock.today(),
        },
    )


@router.get("/cartoes/{card_id}/editar")
def edit(request: Request, card_id: str, session: VerifiedSession, svc: Svc) -> Response:
    c = cards(svc).get(session.user.id, card_id)
    values = {"name": c.name, "institution": c.institution or "", "limit": format_input_brl(c.limit_cents),
              "closing_day": str(c.closing_day), "due_day": str(c.due_day), "last4": c.last4 or "", "color": c.color}
    return form_view(request, CARD_FORM, _card_ctx(c, values, {}), title="Editar cartão")


@router.post("/cartoes/{card_id}", dependencies=write_guard)
async def update(request: Request, card_id: str, session: VerifiedSession, svc: Svc) -> Response:
    c = cards(svc).get(session.user.id, card_id)
    form = await request.form()
    try:
        cards(svc).update(session.user.id, card_id, _read_card(form))
    except AppError as exc:
        return form_view(request, CARD_FORM, _card_ctx(c, form_values(form), exc.field_errors, exc.detail), title="Editar cartão", status_code=422)
    return success(request, "Cartão atualizado.", fallback_url=f"/app/cartoes/{card_id}")


@router.post("/cartoes/{card_id}/arquivar", dependencies=write_guard)
def archive(request: Request, card_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        cards(svc).set_archived(session.user.id, card_id, True)
    except AppError as exc:
        return failure(request, exc, fallback_url=f"/app/cartoes/{card_id}")
    return success(request, "Cartão arquivado.", fallback_url="/app/cartoes")


# ---------------------------------------------------------------- compras
def _purchase_ctx(svc: Svc, user_id: Any, card: CreditCard, values: dict[str, Any], errors: dict[str, str], error: str | None = None) -> dict[str, Any]:
    return {"card": card, "values": values, "errors": errors, "error": error,
            "categories": svc.categories.list(user_id, kind=CategoryKind.EXPENSE)}


@router.get("/cartoes/{card_id}/compras/nova")
def purchase_form(request: Request, card_id: str, session: VerifiedSession, svc: Svc) -> Response:
    card = cards(svc).get(session.user.id, card_id)
    values = {"purchase_date": svc.clock.today().isoformat(), "installments": "1"}
    return form_view(request, PURCHASE_FORM, _purchase_ctx(svc, session.user.id, card, values, {}), title="Nova compra")


@router.get("/cartoes/{card_id}/compras/simular")
def simulate(request: Request, card_id: str, session: VerifiedSession, svc: Svc) -> Response:
    """Pré-visualização do cronograma de parcelas (mesma regra de domínio usada ao gravar)."""
    card = cards(svc).get(session.user.id, card_id)
    reader = FormReader(request.query_params)  # type: ignore[arg-type]
    total = reader.money("amount")
    count = reader.integer("installments", 1, min_value=1, max_value=48)
    when = reader.date("purchase_date")
    plan = []
    if not reader.errors and total >= count and when:
        plan = plan_purchase(when, total, count, card.closing_day, card.due_day)
    return page(request, "partials/cards/simulation.html", {"plan": plan})


@router.post("/cartoes/{card_id}/compras", dependencies=write_guard)
async def purchase_create(request: Request, card_id: str, session: VerifiedSession, svc: Svc) -> Response:
    card = cards(svc).get(session.user.id, card_id)
    form = await request.form()
    reader = FormReader(form)
    try:
        data = PurchaseInput(
            category_id=reader.raw("category_id"),
            description=reader.text("description"),
            purchase_date=reader.required_date("purchase_date", svc.clock.today()),
            total_cents=reader.money("amount"),
            installments=reader.integer("installments", 1, min_value=1, max_value=48),
        )
        reader.raise_if_errors()
        cards(svc).purchase(session.user.id, card_id, data, idempotency_key=reader.raw("idempotency_key"))
    except AppError as exc:
        ctx = _purchase_ctx(svc, session.user.id, card, form_values(form), exc.field_errors, exc.detail)
        return form_view(request, PURCHASE_FORM, ctx, title="Nova compra", status_code=422)
    return success(request, "Compra registrada.", fallback_url=f"/app/cartoes/{card_id}")


@router.post("/compras/{purchase_id}/estornar", dependencies=write_guard)
def refund(request: Request, purchase_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        purchase = cards(svc).refund(session.user.id, purchase_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/cartoes")
    return success(request, "Compra estornada.", fallback_url=f"/app/cartoes/{purchase.card_id}")


# ---------------------------------------------------------------- faturas
@router.get("/faturas/{statement_id}")
def statement_detail(request: Request, statement_id: str, session: VerifiedSession, svc: Svc) -> Response:
    service = cards(svc)
    statement, balance = service.statement(session.user.id, statement_id)
    return page(
        request,
        "pages/cards/statement.html",
        {
            "statement": statement,
            "balance": balance,
            "card": service.get(session.user.id, statement.card_id),
            "items": service.statement_items(statement),
            "payments": service.statement_payments(statement),
            "today": svc.clock.today(),
        },
    )


@router.get("/faturas/{statement_id}/pagar")
def pay_form(request: Request, statement_id: str, session: VerifiedSession, svc: Svc) -> Response:
    statement, balance = cards(svc).statement(session.user.id, statement_id)
    values = {"amount": format_input_brl(max(0, balance.remaining_cents)), "payment_date": svc.clock.today().isoformat()}
    ctx = {"statement": statement, "balance": balance, "values": values, "errors": {}, "accounts": svc.accounts.list(session.user.id)}
    return form_view(request, PAY_FORM, ctx, title="Pagar fatura")


@router.post("/faturas/{statement_id}/pagar", dependencies=write_guard)
async def pay(request: Request, statement_id: str, session: VerifiedSession, svc: Svc) -> Response:
    statement, balance = cards(svc).statement(session.user.id, statement_id)
    form = await request.form()
    reader = FormReader(form)
    try:
        amount = reader.money("amount")
        when = reader.required_date("payment_date", svc.clock.today())
        reader.raise_if_errors()
        cards(svc).pay(session.user.id, statement_id, reader.raw("account_id") or None, amount, when, idempotency_key=reader.raw("idempotency_key"))
    except AppError as exc:
        ctx = {"statement": statement, "balance": balance, "values": form_values(form), "errors": exc.field_errors,
               "error": exc.detail, "accounts": svc.accounts.list(session.user.id)}
        return form_view(request, PAY_FORM, ctx, title="Pagar fatura", status_code=422)
    return success(request, "Pagamento registrado.", fallback_url=f"/app/faturas/{statement_id}")


@router.post("/pagamentos/{payment_id}/cancelar", dependencies=write_guard)
def cancel_payment(request: Request, payment_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        payment = cards(svc).cancel_payment(session.user.id, payment_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/cartoes")
    return success(request, "Pagamento cancelado e valor devolvido à conta.", fallback_url=f"/app/faturas/{payment.statement_id}")
