"""Tratamento global de exceções: problem+json na API, páginas amigáveis na web.

Nunca expõe stack traces ou detalhes internos ao usuário final.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, AuthenticationRequired, EmailNotVerified, RateLimited

logger = logging.getLogger(__name__)

HTTP_TITLES = {
    400: "Requisição inválida",
    401: "Autenticação necessária",
    403: "Acesso negado",
    404: "Página não encontrada",
    405: "Método não permitido",
    413: "Conteúdo muito grande",
    422: "Dados inválidos",
    429: "Muitas requisições",
    500: "Erro interno",
}


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _problem(
    request: Request,
    status: int,
    title: str,
    detail: str,
    *,
    code: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://finpilot.local/problems/{code or status}",
        "title": title,
        "status": status,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }
    if code:
        body["code"] = code
    if errors:
        body["errors"] = errors
    return JSONResponse(
        body, status_code=status, media_type="application/problem+json", headers=headers
    )


def _error_page(
    request: Request, status: int, title: str, detail: str, headers: dict[str, str] | None = None
) -> Response:
    from app.web.render import is_htmx, page, toast_header  # noqa: PLC0415 - evita ciclo de import

    if is_htmx(request):
        merged = {**toast_header(detail, "error"), **(headers or {}), "HX-Reswap": "none"}
        return Response(status_code=status, headers=merged)
    return page(
        request,
        "pages/error.html",
        {"status": status, "title": title, "detail": detail},
        status_code=status,
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> Response:
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
        if _is_api(request):
            errors = [{"field": k, "message": v} for k, v in exc.field_errors.items()] or None
            return _problem(
                request,
                exc.status_code,
                exc.title,
                exc.detail,
                code=exc.code,
                errors=errors,
                headers=headers,
            )
        if isinstance(exc, AuthenticationRequired) and exc.code == "auth_required":
            target = "/login?next=" + quote(request.url.path, safe="/")
            if request.headers.get("hx-request") == "true":
                return Response(status_code=204, headers={"HX-Redirect": target})
            return RedirectResponse(target, status_code=303)
        if isinstance(exc, EmailNotVerified):
            return RedirectResponse("/verifique-email", status_code=303)
        return _error_page(request, exc.status_code, exc.title, exc.detail, headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> Response:
        errors = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query")),
                "message": err.get("msg", "inválido"),
            }
            for err in exc.errors()
        ]
        if _is_api(request):
            return _problem(
                request,
                422,
                "Dados inválidos",
                "Revise os campos informados.",
                code="validation",
                errors=errors,
            )
        return _error_page(request, 422, "Dados inválidos", "Os dados enviados são inválidos.")

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> Response:
        title = HTTP_TITLES.get(exc.status_code, "Erro")
        detail = exc.detail if isinstance(exc.detail, str) and exc.status_code != 500 else title
        if _is_api(request):
            return _problem(request, exc.status_code, title, detail)
        if exc.status_code == 404:
            detail = "O endereço acessado não existe ou não está disponível para sua conta."
        return _error_page(request, exc.status_code, title, detail)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("erro não tratado", extra={"path": request.url.path})
        detail = "Ocorreu um erro inesperado. Informe o código da requisição ao suporte."
        if _is_api(request):
            return _problem(request, 500, "Erro interno", detail)
        try:
            return _error_page(request, 500, "Erro interno", detail)
        except Exception:
            logger.exception("falha ao renderizar página de erro")
            return Response("Erro interno", status_code=500, media_type="text/plain")
