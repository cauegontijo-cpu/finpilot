"""Ponto de entrada da aplicação FastAPI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.clock import Clock
from app.core.config import Settings, get_settings
from app.core.deps import csrf_protect
from app.core.handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.mailer import build_mailer
from app.core.middleware import (
    BodySizeLimitMiddleware,
    HealthAwareTrustedHostMiddleware,
    RequestContextMiddleware,
)
from app.core.ratelimit import MemoryRateLimiter, RedisRateLimiter
from app.core.templating import APP_DIR
from app.web.router import web_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("FinPilot iniciado", extra={"env": settings.env.value})
        stop = None
        if settings.embedded_scheduler:
            from app.jobs.scheduler import start_embedded  # noqa: PLC0415 - só quando habilitado

            stop = start_embedded()
        yield
        if stop is not None:
            stop.set()

    docs = settings.api_docs_enabled
    app = FastAPI(
        title="FinPilot API",
        version="1.0.0",
        description=(
            "API REST do FinPilot. Autenticação por cookie de sessão (HttpOnly) + "
            "cabeçalho `X-CSRF-Token` em operações mutáveis. Operações financeiras críticas "
            "exigem `Idempotency-Key`. Valores monetários em centavos (`*_cents`)."
        ),
        debug=settings.debug and not settings.is_production,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
        lifespan=lifespan,
        dependencies=[Depends(csrf_protect)],
    )
    app.state.settings = settings
    app.state.clock = Clock()
    app.state.mailer = build_mailer(settings)
    if settings.rate_limit_backend == "redis" and settings.redis_url is not None:
        app.state.rate_limiter = RedisRateLimiter(str(settings.redis_url))
    else:
        app.state.rate_limiter = MemoryRateLimiter()

    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=256 * 1024,
        upload_limit=settings.import_max_bytes + 64 * 1024,
    )
    app.add_middleware(RequestContextMiddleware, settings=settings)
    if settings.is_production:
        # 127.0.0.1/localhost: health check interno do container (o proxy envia o Host público).
        app.add_middleware(
            HealthAwareTrustedHostMiddleware,
            allowed_hosts=[*settings.allowed_hosts, "127.0.0.1", "localhost"],
        )

    register_exception_handlers(app)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(web_router)
    return app


app = create_app()
