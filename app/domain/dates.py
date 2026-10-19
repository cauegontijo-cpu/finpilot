"""Datas de negócio, competências e fuso de apresentação (RN037).

Timestamps de eventos são sempre UTC. Datas de negócio (competência, vencimento,
ocorrência) são `date` puros, derivados no fuso de apresentação (America/Sao_Paulo).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("America/Sao_Paulo")

MONTH_NAMES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
MONTH_ABBR = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_date(moment: datetime, tz: ZoneInfo = APP_TZ) -> date:
    """Data civil do instante no fuso de apresentação."""
    if moment.tzinfo is None:
        raise ValueError("datetime sem fuso horário")
    return moment.astimezone(tz).date()


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def clamp_day(year: int, month: int, day: int) -> date:
    """Data com o dia limitado ao último dia válido do mês (RN046)."""
    return date(year, month, min(day, last_day_of_month(year, month)))


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


@dataclass(frozen=True, order=True)
class Month:
    """Competência mensal (ano, mês)."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError("mês inválido")
        if not 1900 <= self.year <= 2999:
            raise ValueError("ano fora do intervalo aceito")

    @classmethod
    def of(cls, d: date) -> Month:
        return cls(d.year, d.month)

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, last_day_of_month(self.year, self.month))

    @property
    def next_first_day(self) -> date:
        return self.shift(1).first_day

    def shift(self, delta: int) -> Month:
        return Month(*add_months(self.year, self.month, delta))

    def label(self) -> str:
        return f"{MONTH_NAMES[self.month - 1]} de {self.year}"

    def short_label(self) -> str:
        return f"{MONTH_ABBR[self.month - 1]}/{self.year % 100:02d}"

    def iso(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @classmethod
    def parse(cls, text: str) -> Month:
        year, month = text.split("-")
        return cls(int(year), int(month))


def month_range(start: Month, end: Month) -> list[Month]:
    months: list[Month] = []
    current = start
    while current <= end:
        months.append(current)
        current = current.shift(1)
    return months


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def format_date_br(d: date | datetime | None) -> str:
    if d is None:
        return "—"
    if isinstance(d, datetime):
        d = d.astimezone(APP_TZ)
        return d.strftime("%d/%m/%Y %H:%M")
    return d.strftime("%d/%m/%Y")
