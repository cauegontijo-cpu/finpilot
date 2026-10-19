"""Relatórios (HTML, CSV, PDF, impressão) e trilha de auditoria do usuário."""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from app.core.deps import Svc, VerifiedSession
from app.core.errors import NotFoundError, ValidationFailed
from app.domain.dates import Month
from app.models import AuditLog, CategoryKind
from app.repositories.base import parse_uuid
from app.services.exporters import format_cell, to_csv, to_pdf
from app.services.reports import REPORTS, ReportParams, ReportResult, default_params
from app.web.render import page

router = APIRouter(prefix="/app", include_in_schema=False)
HTML_PAGE_SIZE = 100


def _month(value: str | None, fallback: Month) -> Month:
    if not value:
        return fallback
    try:
        return Month.parse(value)
    except (ValueError, TypeError):
        return fallback


def params_from_request(request: Request, svc: Svc, user_id: uuid.UUID) -> ReportParams:
    q = request.query_params
    defaults = default_params(svc.clock.today())
    account_id = parse_uuid(q.get("account_id"))
    category_id = parse_uuid(q.get("category_id"))
    # Filtros só aceitam recursos do próprio usuário (404 caso contrário).
    if account_id:
        svc.accounts.get(user_id, account_id)
    if category_id:
        svc.categories.get(user_id, category_id)
    return ReportParams(
        start=_month(q.get("start"), defaults.start),
        end=_month(q.get("end"), defaults.end),
        account_id=account_id,
        category_id=category_id,
        compare_a=_month(q.get("a"), defaults.end.shift(-1)) if q.get("a") else None,
        compare_b=_month(q.get("b"), defaults.end) if q.get("b") else None,
    )


@router.get("/relatorios")
def reports_index(request: Request, session: VerifiedSession) -> Response:
    return page(request, "pages/reports/index.html", {"reports": REPORTS})


def _filters_context(svc: Svc, user_id: uuid.UUID) -> dict[str, object]:
    return {
        "accounts": svc.accounts.list(user_id, include_archived=True),
        "categories": svc.categories.list(
            user_id, kind=CategoryKind.EXPENSE, include_archived=True
        ),
    }


@router.get("/relatorios/{key}")
def report_view(request: Request, key: str, session: VerifiedSession, svc: Svc) -> Response:
    if key not in REPORTS:
        raise NotFoundError("Relatório não encontrado.")
    try:
        params = params_from_request(request, svc, session.user.id)
        result = svc.reports.build(session.user.id, key, params)
        error = None
    except ValidationFailed as exc:
        params = default_params(svc.clock.today())
        result = svc.reports.build(session.user.id, key, params)
        error = exc.detail
    try:
        page_number = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page_number = 1
    total_rows = len(result.rows)
    pages = max(1, -(-total_rows // HTML_PAGE_SIZE))
    page_number = min(page_number, pages)
    visible = result.rows[(page_number - 1) * HTML_PAGE_SIZE : page_number * HTML_PAGE_SIZE]
    query = {k: v for k, v in request.query_params.items() if k != "page" and v}
    printing = request.query_params.get("imprimir") == "1"
    context = {
        "r": result,
        "key": key,
        "params": params,
        "rows": visible,
        "page": page_number,
        "pages": pages,
        "total_rows": total_rows,
        "query": urlencode(query),
        "fmt": format_cell,
        "error": error,
        "printing": printing,
        "issued_at": svc.clock.now(),
        **_filters_context(svc, session.user.id),
    }
    return page(
        request, "pages/reports/print.html" if printing else "pages/reports/view.html", context
    )


def _export_result(
    request: Request, key: str, session: VerifiedSession, svc: Svc
) -> tuple[ReportResult, ReportParams]:
    if key not in REPORTS:
        raise NotFoundError("Relatório não encontrado.")
    params = params_from_request(request, svc, session.user.id)
    result = svc.reports.build(session.user.id, key, params, limit=svc.settings.export_max_rows)
    if len(result.rows) > svc.settings.export_max_rows:
        raise ValidationFailed(
            "Exportação acima do limite de linhas. Reduza o período.", code="export_limit"
        )
    svc.audit.record(
        "report.exported",
        user_id=session.user.id,
        entity_type="report",
        changes={"relatorio": key, "periodo": params.label},
    )
    svc.db.commit()
    return result, params


@router.get("/relatorios/{key}/exportar.csv")
def export_csv(request: Request, key: str, session: VerifiedSession, svc: Svc) -> Response:
    result, params = _export_result(request, key, session, svc)
    filename = f"finpilot-{key}-{params.start.iso()}-{params.end.iso()}.csv"
    return Response(
        to_csv(result, svc.clock.now()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/relatorios/{key}/exportar.pdf")
def export_pdf(request: Request, key: str, session: VerifiedSession, svc: Svc) -> Response:
    result, params = _export_result(request, key, session, svc)
    filename = f"finpilot-{key}-{params.start.iso()}-{params.end.iso()}.pdf"
    return Response(
        to_pdf(result, svc.clock.now(), session.user.full_name),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------- auditoria
@router.get("/auditoria")
def audit_index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    try:
        page_number = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page_number = 1
    page_size = 50
    total = (
        svc.db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.user_id == session.user.id)
        )
        or 0
    )
    logs = svc.audit.list_for_user(
        session.user.id, limit=page_size, offset=(page_number - 1) * page_size
    )
    return page(
        request,
        "pages/audit.html",
        {
            "logs": logs,
            "page": page_number,
            "pages": max(1, -(-total // page_size)),
            "total": total,
        },
    )
