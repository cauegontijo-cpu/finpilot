"""Padrões de resposta das telas com HTMX + fallback sem JavaScript."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from starlette.datastructures import FormData

from app.core.errors import AppError, NotFoundError
from app.web.render import is_htmx, page, redirect


def form_view(
    request: Request,
    partial: str,
    context: dict[str, Any],
    *,
    title: str,
    status_code: int = 200,
) -> Response:
    """Formulário em drawer/modal (HTMX) ou página completa (sem JS)."""
    if is_htmx(request):
        return page(request, partial, context, status_code=status_code)
    return page(
        request,
        "pages/form_page.html",
        {**context, "partial": partial, "form_title": title},
        status_code=status_code,
    )


def success(
    request: Request, message: str, *, fallback_url: str, events: dict[str, Any] | None = None
) -> Response:
    """Sucesso: em HTMX fecha o diálogo, mostra toast e dispara atualização das listas."""
    if is_htmx(request):
        payload = {"toast": {"message": message, "kind": "success"}, "closeDialog": True, "refresh": True}
        payload.update(events or {})
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(payload, ensure_ascii=True)})
    return redirect(request, fallback_url, message=message)


def failure(request: Request, exc: AppError, *, fallback_url: str) -> Response:
    """Erro de regra de negócio em ações simples (sem formulário a reexibir).

    Recurso inexistente/de outro usuário continua sendo 404 (handler global).
    """
    if isinstance(exc, NotFoundError):
        raise exc
    if is_htmx(request):
        payload = {"toast": {"message": exc.detail, "kind": "error"}}
        return Response(
            status_code=exc.status_code,
            headers={"HX-Trigger": json.dumps(payload, ensure_ascii=True), "HX-Reswap": "none"},
        )
    return redirect(request, fallback_url, message=exc.detail, kind="error")


def form_values(form: FormData) -> dict[str, str]:
    """Valores enviados (para reexibir o formulário após erro); ignora campos técnicos."""
    return {
        k: v for k, v in form.items() if isinstance(v, str) and k not in {"csrf_token", "password"}
    }
