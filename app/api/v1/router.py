"""Agrega os recursos da API REST v1."""

from fastapi import APIRouter

from app.api.v1 import auth, dashboard, finance

api_router = APIRouter()
for module in (auth, finance, dashboard):
    api_router.include_router(module.router)
