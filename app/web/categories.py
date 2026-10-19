"""Telas de categorias."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.deps import Svc, VerifiedSession, financial_rate_limit
from app.core.errors import AppError
from app.models import Category, CategoryKind
from app.services.categories import KIND_LABELS, CategoryInput
from app.utils.validation import PALETTE, SELECTABLE_ICONS
from app.web.common import failure, form_values, form_view, success
from app.web.forms import FormReader
from app.web.render import page

router = APIRouter(prefix="/app/categorias", include_in_schema=False)
write_guard = [Depends(financial_rate_limit)]
PARTIAL = "partials/categories/form.html"


def _ctx(
    category: Category | None,
    values: dict[str, Any],
    errors: dict[str, str],
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "values": values,
        "errors": errors,
        "error": error,
        "kind_options": [(k.value, label) for k, label in KIND_LABELS.items()],
        "palette": PALETTE,
        "icons": SELECTABLE_ICONS,
    }


def _read(form: Any) -> CategoryInput:
    reader = FormReader(form)
    data = CategoryInput(
        name=reader.text("name"),
        kind=reader.enum("kind", CategoryKind, CategoryKind.EXPENSE),
        color=reader.text("color", "#64748b"),
        icon=reader.text("icon", "tag"),
    )
    reader.raise_if_errors()
    return data


@router.get("")
def index(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    categories = svc.categories.list(session.user.id, include_archived=True)
    return page(
        request,
        "pages/categories/index.html",
        {
            "groups": [
                (
                    "Despesas",
                    [c for c in categories if c.kind is CategoryKind.EXPENSE and c.is_active],
                ),
                (
                    "Receitas",
                    [c for c in categories if c.kind is CategoryKind.INCOME and c.is_active],
                ),
            ],
            "archived": [c for c in categories if not c.is_active],
            "kind_labels": KIND_LABELS,
        },
    )


@router.get("/nova")
def new(request: Request, session: VerifiedSession, kind: str = "expense") -> Response:
    values = {
        "kind": kind if kind in ("income", "expense") else "expense",
        "color": "#2563eb",
        "icon": "tag",
    }
    return form_view(request, PARTIAL, _ctx(None, values, {}), title="Nova categoria")


@router.post("", dependencies=write_guard)
async def create(request: Request, session: VerifiedSession, svc: Svc) -> Response:
    form = await request.form()
    try:
        svc.categories.create(session.user.id, _read(form))
    except AppError as exc:
        return form_view(
            request,
            PARTIAL,
            _ctx(None, form_values(form), exc.field_errors, exc.detail),
            title="Nova categoria",
            status_code=422,
        )
    return success(request, "Categoria criada.", fallback_url="/app/categorias")


@router.get("/{category_id}/editar")
def edit(request: Request, category_id: str, session: VerifiedSession, svc: Svc) -> Response:
    c = svc.categories.get(session.user.id, category_id)
    values = {"name": c.name, "kind": c.kind.value, "color": c.color, "icon": c.icon}
    return form_view(request, PARTIAL, _ctx(c, values, {}), title="Editar categoria")


@router.post("/{category_id}", dependencies=write_guard)
async def update(
    request: Request, category_id: str, session: VerifiedSession, svc: Svc
) -> Response:
    c = svc.categories.get(session.user.id, category_id)
    form = await request.form()
    try:
        svc.categories.update(session.user.id, category_id, _read(form))
    except AppError as exc:
        return form_view(
            request,
            PARTIAL,
            _ctx(c, form_values(form), exc.field_errors, exc.detail),
            title="Editar categoria",
            status_code=422,
        )
    return success(request, "Categoria atualizada.", fallback_url="/app/categorias")


@router.post("/{category_id}/arquivar", dependencies=write_guard)
def archive(request: Request, category_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.categories.set_archived(session.user.id, category_id, True)
    return success(
        request,
        "Categoria arquivada. Os lançamentos antigos foram preservados.",
        fallback_url="/app/categorias",
    )


@router.post("/{category_id}/reativar", dependencies=write_guard)
def restore(request: Request, category_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.categories.set_archived(session.user.id, category_id, False)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/categorias")
    return success(request, "Categoria reativada.", fallback_url="/app/categorias")


@router.post("/{category_id}/excluir", dependencies=write_guard)
def delete(request: Request, category_id: str, session: VerifiedSession, svc: Svc) -> Response:
    try:
        svc.categories.delete(session.user.id, category_id)
    except AppError as exc:
        return failure(request, exc, fallback_url="/app/categorias")
    return success(request, "Categoria excluída.", fallback_url="/app/categorias")
