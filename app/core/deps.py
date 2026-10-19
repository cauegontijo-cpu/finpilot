"""Dependências compartilhadas por rotas web e API."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.clock import Clock
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import AuthenticationRequired, CsrfError, EmailNotVerified
from app.core.mailer import Mailer
from app.core.ratelimit import RateLimiter
from app.core.security import csrf_token_for, tokens_match
from app.services.audit import AuditService, RequestMeta
from app.services.auth import AuthenticatedSession, AuthService
from app.services.container import Services

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def get_mailer(request: Request) -> Mailer:
    mailer: Mailer = request.app.state.mailer
    return mailer


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


AppClock = Annotated[Clock, Depends(get_clock)]
Limiter = Annotated[RateLimiter, Depends(get_limiter)]
Meta = Annotated[RequestMeta, Depends(get_request_meta)]


def get_audit(db: DbSession, meta: Meta) -> AuditService:
    return AuditService(db, meta)


def get_auth_service(
    db: DbSession,
    settings: AppSettings,
    clock: AppClock,
    limiter: Limiter,
    mailer: Annotated[Mailer, Depends(get_mailer)],
    meta: Meta,
) -> AuthService:
    return AuthService(db, settings, clock, limiter, mailer, meta)


Audit = Annotated[AuditService, Depends(get_audit)]
Auth = Annotated[AuthService, Depends(get_auth_service)]


def session_cookie(request: Request, settings: Settings) -> str | None:
    return request.cookies.get(settings.session_cookie_effective_name)


def get_optional_session(
    request: Request, auth: Auth, settings: AppSettings
) -> AuthenticatedSession | None:
    if hasattr(request.state, "auth"):
        cached: AuthenticatedSession | None = request.state.auth
        return cached
    resolved = auth.resolve_session(session_cookie(request, settings))
    request.state.auth = resolved
    return resolved


OptionalSession = Annotated[AuthenticatedSession | None, Depends(get_optional_session)]


def require_session(session: OptionalSession) -> AuthenticatedSession:
    if session is None:
        raise AuthenticationRequired
    return session


CurrentSession = Annotated[AuthenticatedSession, Depends(require_session)]


def require_verified_session(session: CurrentSession) -> AuthenticatedSession:
    if not session.user.is_verified:
        raise EmailNotVerified
    return session


VerifiedSession = Annotated[AuthenticatedSession, Depends(require_verified_session)]


def financial_rate_limit(session: VerifiedSession, limiter: Limiter) -> None:
    limiter.hit(ratelimit.FINANCIAL_WRITE, str(session.user.id))


# ---------------------------------------------------------------- CSRF
def csrf_binding(request: Request, settings: Settings) -> str:
    """O token CSRF é ligado à sessão (se houver cookie) ou ao visitante anônimo."""
    raw = session_cookie(request, settings)
    if raw:
        return f"s:{raw}"
    return f"a:{getattr(request.state, 'anon_id', '')}"


def csrf_token(request: Request) -> str:
    settings = get_settings()
    return csrf_token_for(settings.secret_key.get_secret_value(), csrf_binding(request, settings))


def _origin_allowed(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer is None:
            return True  # clientes não-navegador; o token continua obrigatório
        parts = urlsplit(referer)
        origin = f"{parts.scheme}://{parts.netloc}"
    base = urlsplit(settings.base_url)
    allowed = {f"{base.scheme}://{base.netloc}"}
    if not settings.is_production:
        allowed.add(f"{request.url.scheme}://{request.url.netloc}")
    return origin in allowed


async def csrf_protect(request: Request) -> None:
    """Aplicado globalmente: toda requisição mutável exige token CSRF válido (inclui HTMX e API)."""
    if request.method in SAFE_METHODS:
        return
    settings = get_settings()
    if not _origin_allowed(request, settings):
        raise CsrfError
    provided = request.headers.get(CSRF_HEADER)
    if provided is None:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            value = form.get(CSRF_FIELD)
            provided = value if isinstance(value, str) else None
    if not tokens_match(provided, csrf_token(request)):
        raise CsrfError


# ---------------------------------------------------------------- serviços
def get_services(db: DbSession, clock: AppClock, meta: Meta, settings: AppSettings) -> Services:
    return Services(db, clock, meta, settings)


Svc = Annotated[Services, Depends(get_services)]
