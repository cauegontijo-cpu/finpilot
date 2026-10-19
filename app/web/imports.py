"""Assistente de importação de extratos CSV (upload → mapeamento → prévia → confirmação)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from app.core import ratelimit
from app.core.deps import Limiter, Svc, VerifiedSession, financial_rate_limit
from app.core.errors import AppError, NotFoundError
from app.models import CategoryKind, ImportStatus
from app.repositories.base import parse_uuid
from app.services.imports import DATE_FORMATS, Mapping
from app.web.forms import FormReader
from app.web.render import page, redirect

router = APIRouter(prefix="/app/importacoes", include_in_schema=False)
write_guard = [Depends(financial_rate_limit)]


def _index(
    request: Request, svc: Svc, user_id: Any, error: str | None = None, status_code: int = 200
) -> Response:
    return page(
        request,
        "pages/imports/index.html",
        {
            "batches": svc.imports.list(user_id),
            "accounts": svc.accounts.list(user_id),
            "error": error,
            "max_kb": svc.settings.import_max_bytes // 1024,
            "max_rows": svc.settings.import_max_rows,
        },
        status_code=status_code,
    )


@router.get("")
def index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    return _index(request, svc, session.user.id)


@router.post("", dependencies=write_guard)
async def upload(
    request: Request, session: VerifiedSession, svc: Svc, limiter: Limiter
) -> Response:
    limiter.hit(ratelimit.UPLOAD, str(session.user.id))
    form = await request.form(max_files=1, max_fields=10)
    file = form.get("file")
    if not isinstance(file, UploadFile):
        return _index(request, svc, session.user.id, "Selecione um arquivo CSV.", 422)
    # Lê no máximo o limite + 1 byte: arquivos maiores são recusados sem carregar o restante.
    content = await file.read(svc.settings.import_max_bytes + 1)
    await file.close()
    try:
        batch = svc.imports.upload(
            session.user.id, FormReader(form).raw("account_id") or None, file.filename, content
        )
    except AppError as exc:
        return _index(request, svc, session.user.id, exc.detail, exc.status_code)
    return redirect(
        request,
        f"/app/importacoes/{batch.id}",
        message="Arquivo recebido. Confira o mapeamento das colunas.",
    )


@router.get("/{batch_id}")
def detail(request: Request, batch_id: str, session: VerifiedSession, svc: Svc) -> Response:
    batch = svc.imports.get(session.user.id, batch_id)
    rows = svc.imports.rows(batch)
    account = svc.accounts.get(session.user.id, batch.account_id)
    mapping = batch.mapping or svc.imports.guess_mapping(batch)
    categories = svc.categories.list(session.user.id)
    context: dict[str, Any] = {
        "batch": batch,
        "account": account,
        "rows": rows,
        "sample": rows[:6],
        "mapping": mapping,
        "date_formats": list(DATE_FORMATS),
        "income_categories": [c for c in categories if c.kind is CategoryKind.INCOME],
        "expense_categories": [c for c in categories if c.kind is CategoryKind.EXPENSE],
        "previous": svc.imports.previous_import_of_same_file(batch),
        "ambiguous": svc.imports.date_ambiguity(batch, int(mapping["date_col"]))
        if "date_col" in mapping
        else False,
    }
    if batch.status in (ImportStatus.MAPPED, ImportStatus.CONFIRMED):
        context["stats"] = svc.imports.stats(batch)
    return page(request, "pages/imports/detail.html", context)


def _fail(request: Request, exc: AppError, url: str) -> Response:
    if isinstance(exc, NotFoundError):
        raise exc
    return redirect(request, url, message=exc.detail, kind="error")


def _col(reader: FormReader, name: str, optional: bool = False) -> int | None:
    raw = reader.raw(name)
    if raw == "" and optional:
        return None
    try:
        return int(raw)
    except ValueError:
        reader.errors[name] = "Selecione a coluna"
        return None


@router.post("/{batch_id}/mapeamento", dependencies=write_guard)
async def apply_mapping(
    request: Request, batch_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    form = await request.form()
    reader = FormReader(form)
    try:
        mapping = Mapping(
            date_col=_col(reader, "date_col") or 0,
            description_col=_col(reader, "description_col") or 0,
            amount_col=_col(reader, "amount_col") or 0,
            date_format=reader.text("date_format", "dd/mm/aaaa"),
            has_header=reader.bool("has_header"),
            type_col=_col(reader, "type_col", optional=True),
            category_col=_col(reader, "category_col", optional=True),
            external_id_col=_col(reader, "external_id_col", optional=True),
            income_category_id=parse_uuid(reader.raw("income_category_id")),
            expense_category_id=parse_uuid(reader.raw("expense_category_id")),
        )
        reader.raise_if_errors()
        stats = svc.imports.apply_mapping(session.user.id, batch_id, mapping)
    except AppError as exc:
        return _fail(request, exc, f"/app/importacoes/{batch_id}")
    return redirect(
        request,
        f"/app/importacoes/{batch_id}#previa",
        message=f"Prévia pronta: {stats.valid} válidas, {stats.invalid} inválidas, {stats.duplicate + stats.possible_duplicate} possíveis duplicatas.",
    )


@router.post("/{batch_id}/confirmar", dependencies=write_guard)
async def confirm(request: Request, batch_id: str, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    selected = [v for v in form.getlist("rows") if isinstance(v, str)]
    try:
        report = svc.imports.confirm(
            session.user.id,
            batch_id,
            selected,
            idempotency_key=FormReader(form).raw("idempotency_key"),
        )
    except AppError as exc:
        return _fail(request, exc, f"/app/importacoes/{batch_id}")
    return redirect(
        request,
        f"/app/importacoes/{batch_id}",
        message=f"Importação concluída: {report['importadas']} lançamento(s) criados, {report['falhas']} falha(s).",
        kind="success" if not report["falhas"] else "warning",
    )


@router.post("/{batch_id}/descartar", dependencies=write_guard)
def discard(request: Request, batch_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.imports.discard(session.user.id, batch_id)
    except AppError as exc:
        return _fail(request, exc, f"/app/importacoes/{batch_id}")
    return redirect(
        request, "/app/importacoes", message="Importação descartada. Nenhum lançamento foi criado."
    )
