"""Agrega as rotas das páginas web (SSR + HTMX)."""

from fastapi import APIRouter

from app.web import accounts, auth, budgets, categories, dashboard, public, settings, transactions

web_router = APIRouter()
for module in (public, auth, settings, dashboard, accounts, categories, transactions, budgets):
    web_router.include_router(module.router)
