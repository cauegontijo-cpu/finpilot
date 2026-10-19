"""Agrega as rotas das páginas web (SSR + HTMX)."""

from fastapi import APIRouter

from app.web import (
    accounts,
    auth,
    budgets,
    categories,
    dashboard,
    imports,
    planning,
    public,
    reports,
    settings,
    transactions,
)

web_router = APIRouter()
for module in (public, auth, settings, dashboard, accounts, categories, transactions, budgets, planning, imports, reports):
    web_router.include_router(module.router)
