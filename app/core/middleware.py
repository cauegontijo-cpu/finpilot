"""Middlewares ASGI: correlação, log de acesso, cabeçalhos de segurança, visitante anônimo
e limite de tamanho do corpo da requisição."""

from __future__ import annotations

import logging
import re
import secrets
import time
from http.cookies import SimpleCookie

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.logging import request_id_var

access_logger = logging.getLogger("finpilot.access")

ANON_COOKIE = "fp_anon"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
# A página do Swagger UI usa recursos de CDN/inline; aplicamos CSP específica apenas nela.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: "
    "https://fastapi.tiangolo.com; frame-ancestors 'none'"
)


class RequestContextMiddleware:
    """Request id, cabeçalhos de segurança, cookie de visitante e log de acesso."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.match(incoming) else secrets.token_hex(8)
        token = request_id_var.set(request_id)

        cookies = SimpleCookie()
        cookies.load(headers.get(b"cookie", b"").decode("latin-1"))
        anon = cookies.get(ANON_COOKIE)
        anon_value = (
            anon.value if anon and re.fullmatch(r"[A-Za-z0-9_-]{20,64}", anon.value) else None
        )
        new_anon = anon_value is None
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["anon_id"] = anon_value or secrets.token_urlsafe(24)

        path: str = scope.get("path", "")
        is_https = scope.get("scheme") == "https" or headers.get(b"x-forwarded-proto") == b"https"
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = request_id
                h["X-Content-Type-Options"] = "nosniff"
                h["Referrer-Policy"] = "strict-origin-when-cross-origin"
                h["X-Frame-Options"] = "DENY"
                h["Cross-Origin-Opener-Policy"] = "same-origin"
                h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
                h.setdefault(
                    "Content-Security-Policy", DOCS_CSP if path.startswith("/api/docs") else CSP
                )
                if self.settings.is_production and is_https:
                    h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                if not path.startswith("/static/"):
                    h.setdefault("Cache-Control", "no-store")
                if new_anon and not path.startswith(("/static/", "/health")):
                    secure = "; Secure" if self.settings.cookie_secure else ""
                    h.append(
                        "Set-Cookie",
                        f"{ANON_COOKIE}={state['anon_id']}; Path=/; HttpOnly; SameSite=Lax{secure}",
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if not path.startswith(("/static/", "/health")):
                access_logger.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status_holder["status"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    """Rejeita corpos maiores que o limite (413) sem ler o restante do conteúdo."""

    def __init__(self, app: ASGIApp, default_limit: int, upload_limit: int) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        limit = self.upload_limit if "/import" in path else self.default_limit
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject_413(send)
            return

        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            if too_large:
                await _reject_413(send)


class _BodyTooLarge(Exception):
    pass


async def _reject_413(send: Send) -> None:
    body = b'{"title":"Payload muito grande","status":413}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
