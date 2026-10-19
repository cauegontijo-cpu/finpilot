"""Infraestrutura para testes de concorrência: commits reais e várias conexões simultâneas."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

TABLES = [
    "audit_logs",
    "idempotency_keys",
    "card_payments",
    "card_installments",
    "card_purchases",
    "card_statements",
    "credit_cards",
    "import_rows",
    "import_batches",
    "goal_allocations",
    "goals",
    "budgets",
    "transactions",
    "transfers",
    "recurrence_rules",
    "categories",
    "accounts",
    "email_tokens",
    "password_reset_tokens",
    "user_sessions",
    "users",
]


@pytest.fixture
def real_sessions(migrated_engine: Any) -> Iterator[sessionmaker[Session]]:
    maker = sessionmaker(bind=migrated_engine, expire_on_commit=False, autoflush=False)
    yield maker
    with migrated_engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))


def run_concurrently(workers: list[Callable[[], Any]]) -> list[Any]:
    """Executa as funções em threads iniciadas ao mesmo tempo; retorna resultado ou exceção."""
    barrier = threading.Barrier(len(workers))
    results: list[Any] = [None] * len(workers)

    def wrap(i: int, fn: Callable[[], Any]) -> None:
        barrier.wait()
        try:
            results[i] = fn()
        except Exception as exc:  # noqa: BLE001 - o teste inspeciona a exceção
            results[i] = exc

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results
