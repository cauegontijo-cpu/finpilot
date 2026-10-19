"""Agrega as rotas das páginas web (SSR + HTMX)."""

from fastapi import APIRouter

from app.web import accounts, auth, categories, public, settings, transactions

web_router = APIRouter()
for module in (public, auth, settings, accounts, categories, transactions):
    web_router.include_router(module.router)
