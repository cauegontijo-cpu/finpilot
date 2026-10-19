"""Relógio injetável — permite testes determinísticos de expiração e competência."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.domain.dates import local_date


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        """Data civil atual no fuso de apresentação."""
        return local_date(self.now())


class FrozenClock(Clock):
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def set(self, moment: datetime) -> None:
        self._moment = moment

    def advance(self, **kwargs: float) -> None:
        self._moment += timedelta(**kwargs)
