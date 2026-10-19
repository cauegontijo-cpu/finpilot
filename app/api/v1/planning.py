"""API de metas, recorrências, cartões, faturas, importações e relatórios."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile

from app.core import ratelimit
from app.core.deps import Limiter, Svc, VerifiedSession, financial_rate_limit
from app.core.errors import NotFoundError, ValidationFailed
from app.domain.dates import Month
from app.models import CardPurchase
from app.repositories.base import get_owned
from app.schemas.common import PROBLEM_RESPONSES
from app.schemas.planning import (
    AllocationIn,
    AllocationOut,
    CardIn,
    CardOut,
    CardSummaryOut,
    ConfirmIn,
    GoalIn,
    GoalOut,
    ImportBatchOut,
    ImportRowOut,
    MappingIn,
    PaymentIn,
    PaymentOut,
    PurchaseIn,
    PurchaseOut,
    RecurrenceIn,
    RecurrenceOut,
    ReportOut,
    StatementOut,
)
from app.services.card_queries import StatementBalance
from app.services.cards import CardInput, CardService, PurchaseInput
from app.services.exporters import to_csv, to_pdf
from app.services.goals import GoalInput, GoalProgress
from app.services.imports import Mapping
from app.services.recurrences import RecurrenceInput
from app.services.reports import REPORTS, ReportParams

router = APIRouter(responses=PROBLEM_RESPONSES)
Write = Depends(financial_rate_limit)
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=80)]


def _cards(svc: Svc) -> CardService:
    return CardService(svc.db, svc.clock, svc.audit)


# ---------------------------------------------------------------- metas
def _goal_out(p: GoalProgress) -> GoalOut:
    g = p.goal
    return GoalOut(
        id=g.id, name=g.name, description=g.description, target_cents=g.target_cents, deadline=g.deadline, status=g.status,
        allocated_cents=p.allocated, remaining_cents=p.remaining, percent=p.pct, estimated_completion=p.estimated_completion,
        required_monthly_cents=p.required_monthly,
    )


@router.get("/goals", response_model=list[GoalOut], tags=["goals"])
def list_goals(session: VerifiedSession, svc: Svc, include_archived: bool = False) -> list[GoalOut]:
    return [_goal_out(p) for p in svc.goals.list(session.user.id, include_archived=include_archived)]


@router.post("/goals", response_model=GoalOut, status_code=201, tags=["goals"], dependencies=[Write])
def create_goal(data: GoalIn, session: VerifiedSession, svc: Svc) -> GoalOut:
    goal = svc.goals.create(session.user.id, GoalInput(**data.model_dump()))
    return _goal_out(svc.goals.progress(session.user.id, str(goal.id)))


@router.get("/goals/{goal_id}", response_model=GoalOut, tags=["goals"])
def get_goal(goal_id: str, session: VerifiedSession, svc: Svc) -> GoalOut:
    return _goal_out(svc.goals.progress(session.user.id, goal_id))


@router.put("/goals/{goal_id}", response_model=GoalOut, tags=["goals"], dependencies=[Write])
def update_goal(goal_id: str, data: GoalIn, session: VerifiedSession, svc: Svc) -> GoalOut:
    svc.goals.update(session.user.id, goal_id, GoalInput(**data.model_dump()))
    return _goal_out(svc.goals.progress(session.user.id, goal_id))


@router.get("/goals/{goal_id}/allocations", response_model=list[AllocationOut], tags=["goals"])
def list_allocations(goal_id: str, session: VerifiedSession, svc: Svc) -> list[AllocationOut]:
    return [AllocationOut.model_validate(a) for a in svc.goals.allocations(session.user.id, goal_id)]


@router.post("/goals/{goal_id}/allocations", response_model=AllocationOut, status_code=201, tags=["goals"], dependencies=[Write])
def allocate(goal_id: str, data: AllocationIn, session: VerifiedSession, svc: Svc) -> AllocationOut:
    """Reserva virtual — não movimenta contas."""
    return AllocationOut.model_validate(svc.goals.allocate(session.user.id, goal_id, data.amount_cents, data.allocation_date, data.note))


@router.post("/goals/{goal_id}/allocations/{allocation_id}/reverse", response_model=AllocationOut, tags=["goals"], dependencies=[Write])
def reverse_allocation(goal_id: str, allocation_id: str, session: VerifiedSession, svc: Svc) -> AllocationOut:
    return AllocationOut.model_validate(svc.goals.reverse_allocation(session.user.id, goal_id, allocation_id))


@router.post("/goals/{goal_id}/archive", response_model=GoalOut, tags=["goals"], dependencies=[Write])
def archive_goal(goal_id: str, session: VerifiedSession, svc: Svc) -> GoalOut:
    svc.goals.set_archived(session.user.id, goal_id, True)
    return _goal_out(svc.goals.progress(session.user.id, goal_id))


# ---------------------------------------------------------------- recorrências
@router.get("/recurrences", response_model=list[RecurrenceOut], tags=["recurrences"])
def list_recurrences(session: VerifiedSession, svc: Svc) -> list[RecurrenceOut]:
    return [RecurrenceOut.model_validate(r) for r in svc.recurrences.list(session.user.id)]


@router.post("/recurrences", response_model=RecurrenceOut, status_code=201, tags=["recurrences"], dependencies=[Write])
def create_recurrence(data: RecurrenceIn, session: VerifiedSession, svc: Svc) -> RecurrenceOut:
    return RecurrenceOut.model_validate(svc.recurrences.create(session.user.id, RecurrenceInput(**data.model_dump())))


@router.get("/recurrences/{rule_id}", response_model=RecurrenceOut, tags=["recurrences"])
def get_recurrence(rule_id: str, session: VerifiedSession, svc: Svc) -> RecurrenceOut:
    return RecurrenceOut.model_validate(svc.recurrences.get(session.user.id, rule_id))


@router.get("/recurrences/{rule_id}/next", response_model=list[date], tags=["recurrences"])
def next_occurrences(rule_id: str, session: VerifiedSession, svc: Svc, count: Annotated[int, Query(ge=1, le=24)] = 6) -> list[date]:
    return svc.recurrences.next_occurrences(svc.recurrences.get(session.user.id, rule_id), count)


@router.put("/recurrences/{rule_id}", response_model=RecurrenceOut, tags=["recurrences"], dependencies=[Write])
def update_recurrence(rule_id: str, data: RecurrenceIn, session: VerifiedSession, svc: Svc) -> RecurrenceOut:
    """Altera apenas ocorrências futuras ainda previstas."""
    return RecurrenceOut.model_validate(svc.recurrences.update(session.user.id, rule_id, RecurrenceInput(**data.model_dump())))


@router.post("/recurrences/{rule_id}/{action}", response_model=RecurrenceOut, tags=["recurrences"], dependencies=[Write])
def recurrence_action(rule_id: str, action: str, session: VerifiedSession, svc: Svc) -> RecurrenceOut:
    handlers = {"pause": svc.recurrences.pause, "resume": svc.recurrences.resume, "cancel": svc.recurrences.cancel}
    if action not in handlers:
        raise NotFoundError("Ação inexistente.")
    return RecurrenceOut.model_validate(handlers[action](session.user.id, rule_id))


# ---------------------------------------------------------------- cartões
@router.get("/cards", response_model=list[CardSummaryOut], tags=["cards"])
def list_cards(session: VerifiedSession, svc: Svc) -> list[CardSummaryOut]:
    return [
        CardSummaryOut(**CardOut.model_validate(s.card).model_dump(), debt_cents=s.debt, available_cents=s.available)
        for s in _cards(svc).summaries(session.user.id)
    ]


@router.post("/cards", response_model=CardOut, status_code=201, tags=["cards"], dependencies=[Write])
def create_card(data: CardIn, session: VerifiedSession, svc: Svc) -> CardOut:
    return CardOut.model_validate(_cards(svc).create(session.user.id, CardInput(**data.model_dump())))


@router.get("/cards/{card_id}", response_model=CardOut, tags=["cards"])
def get_card(card_id: str, session: VerifiedSession, svc: Svc) -> CardOut:
    return CardOut.model_validate(_cards(svc).get(session.user.id, card_id))


@router.put("/cards/{card_id}", response_model=CardOut, tags=["cards"], dependencies=[Write])
def update_card(card_id: str, data: CardIn, session: VerifiedSession, svc: Svc) -> CardOut:
    return CardOut.model_validate(_cards(svc).update(session.user.id, card_id, CardInput(**data.model_dump())))


@router.get("/cards/{card_id}/purchases", response_model=list[PurchaseOut], tags=["cards"])
def list_purchases(card_id: str, session: VerifiedSession, svc: Svc) -> list[PurchaseOut]:
    return [PurchaseOut.model_validate(p) for p in _cards(svc).purchases(session.user.id, card_id)]


@router.post("/cards/{card_id}/purchases", response_model=PurchaseOut, status_code=201, tags=["cards"], dependencies=[Write])
def create_purchase(card_id: str, data: PurchaseIn, session: VerifiedSession, svc: Svc, idempotency_key: IdemKey) -> PurchaseOut:
    purchase = _cards(svc).purchase(session.user.id, card_id, PurchaseInput(**data.model_dump()), idempotency_key=idempotency_key)
    svc.db.refresh(purchase)
    return PurchaseOut.model_validate(get_owned(svc.db, CardPurchase, purchase.id, session.user.id))


@router.post("/purchases/{purchase_id}/refund", response_model=PurchaseOut, tags=["cards"], dependencies=[Write])
def refund_purchase(purchase_id: str, session: VerifiedSession, svc: Svc) -> PurchaseOut:
    purchase = _cards(svc).refund(session.user.id, purchase_id)
    svc.db.refresh(purchase)
    return PurchaseOut.model_validate(purchase)


def _statement_out(b: StatementBalance, today: date) -> StatementOut:
    return StatementOut(
        statement_id=b.statement_id, card_id=b.card_id, year=b.year, month=b.month, closing_date=b.closing_date,
        due_date=b.due_date, total_cents=b.total_cents, paid_cents=b.paid_cents, remaining_cents=b.remaining_cents,
        status=b.status(today),
    )


@router.get("/cards/{card_id}/statements", response_model=list[StatementOut], tags=["statements"])
def list_statements(card_id: str, session: VerifiedSession, svc: Svc) -> list[StatementOut]:
    today = svc.clock.today()
    return [_statement_out(b, today) for b in _cards(svc).statements(session.user.id, card_id)]


@router.get("/statements/{statement_id}", response_model=StatementOut, tags=["statements"])
def get_statement(statement_id: str, session: VerifiedSession, svc: Svc) -> StatementOut:
    _, balance = _cards(svc).statement(session.user.id, statement_id)
    return _statement_out(balance, svc.clock.today())


@router.post("/statements/{statement_id}/payments", response_model=PaymentOut, status_code=201, tags=["statements"], dependencies=[Write])
def pay_statement(statement_id: str, data: PaymentIn, session: VerifiedSession, svc: Svc, idempotency_key: IdemKey) -> PaymentOut:
    """Saída de caixa da conta (não é despesa). Pagamento acima do devido é recusado."""
    payment = _cards(svc).pay(session.user.id, statement_id, data.account_id, data.amount_cents, data.payment_date, idempotency_key=idempotency_key)
    return PaymentOut.model_validate(payment)


@router.post("/payments/{payment_id}/cancel", response_model=PaymentOut, tags=["statements"], dependencies=[Write])
def cancel_payment(payment_id: str, session: VerifiedSession, svc: Svc) -> PaymentOut:
    return PaymentOut.model_validate(_cards(svc).cancel_payment(session.user.id, payment_id))


# ---------------------------------------------------------------- importações
@router.post("/imports", response_model=ImportBatchOut, status_code=201, tags=["imports"], dependencies=[Write])
async def upload_import(
    session: VerifiedSession, svc: Svc, limiter: Limiter,
    account_id: Annotated[str, Form()], file: Annotated[UploadFile, File(description="Arquivo .csv")],
) -> ImportBatchOut:
    limiter.hit(ratelimit.UPLOAD, str(session.user.id))
    content = await file.read(svc.settings.import_max_bytes + 1)
    batch = svc.imports.upload(session.user.id, account_id, file.filename, content)
    return ImportBatchOut.model_validate(batch)


@router.get("/imports/{batch_id}", response_model=ImportBatchOut, tags=["imports"])
def get_import(batch_id: str, session: VerifiedSession, svc: Svc) -> ImportBatchOut:
    return ImportBatchOut.model_validate(svc.imports.get(session.user.id, batch_id))


@router.get("/imports/{batch_id}/rows", response_model=list[ImportRowOut], tags=["imports"])
def import_rows(batch_id: str, session: VerifiedSession, svc: Svc) -> list[ImportRowOut]:
    return [ImportRowOut.model_validate(r) for r in svc.imports.rows(svc.imports.get(session.user.id, batch_id))]


@router.post("/imports/{batch_id}/mapping", tags=["imports"], dependencies=[Write])
def map_import(batch_id: str, data: MappingIn, session: VerifiedSession, svc: Svc) -> dict[str, int]:
    stats = svc.imports.apply_mapping(session.user.id, batch_id, Mapping(**data.model_dump()))
    return dict(stats.__dict__)


@router.post("/imports/{batch_id}/confirm", tags=["imports"], dependencies=[Write])
def confirm_import(batch_id: str, data: ConfirmIn, session: VerifiedSession, svc: Svc, idempotency_key: IdemKey) -> dict[str, Any]:
    return svc.imports.confirm(session.user.id, batch_id, [str(i) for i in data.row_ids], idempotency_key=idempotency_key)


@router.delete("/imports/{batch_id}", status_code=204, tags=["imports"], dependencies=[Write])
def discard_import(batch_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.imports.discard(session.user.id, batch_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- relatórios
def _report_params(svc: Svc, user_id: Any, start: str, end: str, account_id: str | None, category_id: str | None) -> ReportParams:
    try:
        params = ReportParams(start=Month.parse(start), end=Month.parse(end))
    except (ValueError, TypeError) as exc:
        raise ValidationFailed("Use o formato AAAA-MM para start e end.", code="invalid_period") from exc
    if account_id:
        params = ReportParams(params.start, params.end, account_id=svc.accounts.get(user_id, account_id).id)
    if category_id:
        params = ReportParams(params.start, params.end, params.account_id, svc.categories.get(user_id, category_id).id)
    return params


@router.get("/reports", tags=["reports"])
def list_reports(session: VerifiedSession) -> list[dict[str, str]]:
    return [{"key": k, "title": t, "regime": r} for k, (t, r) in REPORTS.items()]


@router.get("/reports/{key}", response_model=ReportOut, tags=["reports"])
def get_report(
    key: str, session: VerifiedSession, svc: Svc,
    start: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")], end: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    account_id: str | None = None, category_id: str | None = None,
    output: Annotated[str, Query(alias="format", pattern="^(json|csv|pdf)$")] = "json",
) -> Any:
    if key not in REPORTS:
        raise NotFoundError("Relatório não encontrado.")
    params = _report_params(svc, session.user.id, start, end, account_id, category_id)
    result = svc.reports.build(session.user.id, key, params, limit=svc.settings.export_max_rows)
    if output == "csv":
        return Response(to_csv(result, svc.clock.now()), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="finpilot-{key}.csv"'})
    if output == "pdf":
        return Response(to_pdf(result, svc.clock.now(), session.user.full_name), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="finpilot-{key}.pdf"'})
    return ReportOut(
        key=result.key, title=result.title, regime=result.regime, period=result.period_label,
        columns=[{"label": c.label, "kind": c.kind} for c in result.columns],
        rows=[[str(v) if v is not None and not isinstance(v, int | str) else v for v in row] for row in result.rows],
        totals=[str(v) if v is not None and not isinstance(v, int | str) else v for v in result.totals] if result.totals else None,
        notes=result.notes,
    )
