"""Validações reutilizáveis de entrada (backend é a fonte de verdade — RN019)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from app.core.errors import ValidationFailed, expect

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Ícones que o usuário pode escolher para contas, categorias e metas.
SELECTABLE_ICONS = [
    "wallet",
    "landmark",
    "piggy-bank",
    "banknote",
    "coins",
    "credit-card",
    "smartphone",
    "building-2",
    "briefcase",
    "laptop",
    "chart-line",
    "undo-2",
    "hand-coins",
    "gift",
    "utensils",
    "house",
    "car",
    "heart-pulse",
    "graduation-cap",
    "gamepad-2",
    "tv",
    "shopping-bag",
    "shopping-cart",
    "tag",
    "plane",
    "hammer",
    "zap",
    "wifi",
    "receipt",
    "target",
    "shield-check",
    "sparkles",
    "layers",
    "scale",
]
PALETTE = [
    "#0b1b3f",
    "#2563eb",
    "#0ea5e9",
    "#14b8a6",
    "#10b981",
    "#22c55e",
    "#84cc16",
    "#eab308",
    "#f97316",
    "#ef4444",
    "#ec4899",
    "#8b5cf6",
    "#6366f1",
    "#64748b",
    "#7c3aed",
    "#0d9488",
    "#059669",
]


def clean_text(value: str | None, *, max_len: int) -> str:
    """Normaliza Unicode, remove caracteres de controle e espaços extras."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _CONTROL.sub("", text)
    text = " ".join(text.split())
    if len(text) > max_len:
        raise ValidationFailed(f"Texto excede {max_len} caracteres.")
    return text


def optional_text(value: str | None, *, max_len: int) -> str | None:
    text = clean_text(value, max_len=max_len)
    return text or None


def clean_multiline(value: str | None, *, max_len: int) -> str | None:
    if value is None:
        return None
    text = _CONTROL.sub("", unicodedata.normalize("NFC", str(value))).strip()
    if len(text) > max_len:
        raise ValidationFailed(f"Texto excede {max_len} caracteres.")
    return text or None


def validate_color(value: str | None, default: str = "#64748b") -> str:
    if not value:
        return default
    if not _COLOR.match(value) or value.lower() not in PALETTE:
        # Paleta fechada: cada cor tem classe CSS própria (sem estilos inline — CSP).
        raise ValidationFailed("Cor inválida.", field_errors={"color": "Escolha uma cor da paleta"})
    return value.lower()


def validate_icon(value: str | None, default: str = "tag") -> str:
    if not value:
        return default
    if value not in SELECTABLE_ICONS:
        raise ValidationFailed("Ícone inválido.", field_errors={"icon": "Ícone não disponível"})
    return value


def parse_date(value: str | date | None, field: str, *, required: bool = True) -> date | None:
    if value in (None, ""):
        if required:
            raise ValidationFailed("Data obrigatória.", field_errors={field: "Obrigatória"})
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()  # noqa: DTZ007 - data civil, sem horário
        except ValueError:
            continue
        if not 1900 <= parsed.year <= 2999:
            break
        return parsed
    raise ValidationFailed("Data inválida.", field_errors={field: "Data inválida"})


def require_date(value: str | date | None, field: str) -> date:
    return expect(parse_date(value, field, required=True))
