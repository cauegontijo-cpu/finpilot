"""Renderização de páginas com contexto comum (usuário, CSRF, tema, mensagens flash)."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.responses import HTMLResponse

from app.core.config import get_settings
from app.core.deps import csrf_token
from app.core.security import hmac_hex, tokens_match
from app.core.templating import render as _render

FLASH_COOKIE = "fp_flash"
THEME_COOKIE = "fp_theme"
THEMES = {"light", "dark", "system"}


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _sign(payload: str) -> str:
    return hmac_hex(get_settings().secret_key.get_secret_value(), f"flash:{payload}")[:32]


def set_flash(response: Response, message: str, kind: str = "success") -> None:
    payload = base64.urlsafe_b64encode(json.dumps({"m": message[:300], "k": kind}).encode()).decode()
    response.set_cookie(
        FLASH_COOKIE,
        f"{payload}.{_sign(payload)}",
        max_age=60,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
    )


def _read_flash(request: Request) -> dict[str, str] | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw or "." not in raw:
        return None
    payload, signature = raw.rsplit(".", 1)
    if not tokens_match(signature, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {"message": str(data.get("m", "")), "kind": str(data.get("k", "success"))}


def theme_of(request: Request) -> str:
    value = request.cookies.get(THEME_COOKIE, "system")
    return value if value in THEMES else "system"


def page(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    auth = getattr(request.state, "auth", None)
    flash = _read_flash(request)
    ctx: dict[str, Any] = {
        "csrf_token": csrf_token(request),
        "current_user": auth.user if auth else None,
        "theme": theme_of(request),
        "flash": flash,
        "is_htmx": is_htmx(request),
        "new_key": lambda: uuid.uuid4().hex,
        "app_name": get_settings().app_name,
        "path": request.url.path,
    }
    ctx.update(context or {})
    response: HTMLResponse = _render(request, template, ctx, status_code=status_code, headers=headers)
    if flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect(request: Request, url: str, *, message: str | None = None, kind: str = "success") -> Response:
    """Redireciona (303). Em requisições HTMX usa HX-Redirect para navegação completa."""
    response: Response
    if is_htmx(request):
        response = Response(status_code=204, headers={"HX-Redirect": url})
    else:
        response = RedirectResponse(url, status_code=303)
    if message:
        set_flash(response, message, kind)
    return response


def toast_header(message: str, kind: str = "success") -> dict[str, str]:
    return {"HX-Trigger": json.dumps({"toast": {"message": message, "kind": kind}}, ensure_ascii=True)}
