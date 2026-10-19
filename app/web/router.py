"""Agrega as rotas das páginas web (SSR + HTMX)."""

from fastapi import APIRouter

from app.web import auth, public, settings

web_router = APIRouter()
web_router.include_router(public.router)
web_router.include_router(auth.router)
web_router.include_router(settings.router)
