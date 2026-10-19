"""Páginas públicas, preferência de tema e health checks."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.core.deps import AppSettings, DbSession, OptionalSession
from app.core.ratelimit import RedisRateLimiter
from app.core.security import safe_next_url
from app.web.render import THEME_COOKIE, THEMES, page, redirect

router = APIRouter()


@router.get("/", include_in_schema=False)
def landing(request: Request, session: OptionalSession) -> Response:
    return page(request, "pages/landing.html", {"logged_in": session is not None})


@router.get("/privacidade", include_in_schema=False)
def privacy(request: Request, session: OptionalSession) -> Response:
    return page(request, "pages/privacy.html")


@router.get("/termos", include_in_schema=False)
def terms(request: Request, session: OptionalSession) -> Response:
    return page(request, "pages/terms.html")


@router.post("/preferencias/tema", include_in_schema=False)
def set_theme(
    request: Request,
    settings: AppSettings,
    theme: Annotated[str, Form()] = "system",
    next_url: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    value = theme if theme in THEMES else "system"
    response = redirect(request, safe_next_url(next_url, default="/"))
    response.set_cookie(
        THEME_COOKIE,
        value,
        max_age=365 * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return response


@router.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
def ready(request: Request, db: DbSession) -> Response:
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001 - health check reporta indisponibilidade em vez de falhar
        checks["database"] = "error"
    limiter = request.app.state.rate_limiter
    if isinstance(limiter, RedisRateLimiter):
        try:
            limiter.ping()
            checks["redis"] = "ok"
        except Exception:  # noqa: BLE001
            checks["redis"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", **checks}, status_code=200 if healthy else 503
    )
