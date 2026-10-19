"""Rate limiting com janela fixa (Redis em produção; memória em testes)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

import redis

from app.core.errors import RateLimited


@dataclass(frozen=True)
class Limit:
    name: str
    max_hits: int
    window_seconds: int


# Limites por finalidade (seção 18.4).
LOGIN_IP = Limit("login-ip", 20, 15 * 60)
LOGIN_ACCOUNT = Limit("login-account", 5, 15 * 60)
REGISTER_IP = Limit("register-ip", 5, 60 * 60)
PASSWORD_RESET_IP = Limit("reset-ip", 5, 60 * 60)
PASSWORD_RESET_ACCOUNT = Limit("reset-account", 3, 60 * 60)
VERIFY_EMAIL = Limit("verify-email", 10, 60 * 60)
RESEND_VERIFICATION = Limit("resend-verification", 3, 60 * 60)
UPLOAD = Limit("upload", 20, 60 * 60)
FINANCIAL_WRITE = Limit("financial-write", 120, 60)
SENSITIVE_ACCOUNT = Limit("sensitive-account", 10, 60 * 60)


class RateLimiter(Protocol):
    def hit(self, limit: Limit, key: str) -> None: ...
    def reset(self, limit: Limit, key: str) -> None: ...


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[int, float]] = {}

    def hit(self, limit: Limit, key: str) -> None:
        bucket = f"{limit.name}:{key}"
        now = time.monotonic()
        with self._lock:
            count, started = self._buckets.get(bucket, (0, now))
            if now - started >= limit.window_seconds:
                count, started = 0, now
            count += 1
            self._buckets[bucket] = (count, started)
        if count > limit.max_hits:
            raise RateLimited(max(1, int(limit.window_seconds - (now - started))))

    def reset(self, limit: Limit, key: str) -> None:
        with self._lock:
            self._buckets.pop(f"{limit.name}:{key}", None)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        self._redis = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    def hit(self, limit: Limit, key: str) -> None:
        bucket = f"rl:{limit.name}:{key}"
        pipe = self._redis.pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, limit.window_seconds, nx=True)
        pipe.ttl(bucket)
        count, _, ttl = pipe.execute()
        if int(count) > limit.max_hits:
            raise RateLimited(max(1, int(ttl)))

    def reset(self, limit: Limit, key: str) -> None:
        self._redis.delete(f"rl:{limit.name}:{key}")

    def ping(self) -> bool:
        return bool(self._redis.ping())
