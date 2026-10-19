"""Isolamento dos testes ponta a ponta: zera os contadores de rate limit do Redis de DEV.

Sem isso, execuções repetidas esbarram (corretamente) no limite de cadastros por IP.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    url = os.environ.get("E2E_REDIS_URL", "redis://127.0.0.1:6379/0")
    if os.environ.get("E2E_BASE_URL"):
        import redis

        client = redis.Redis.from_url(url)
        for key in client.scan_iter("rl:*"):
            client.delete(key)
    yield
