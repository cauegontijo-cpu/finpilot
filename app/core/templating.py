"""Configuração do Jinja2: autoescape, filtros pt-BR e ícones Lucide inline."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app.domain.dates import MONTH_NAMES, Month, format_date_br
from app.domain.ledger import ORIGIN_LABELS, STATUS_LABELS, TYPE_LABELS
from app.domain.money import format_brl, format_input_brl, percent

APP_DIR = Path(__file__).resolve().parent.parent
ICON_DIR = APP_DIR / "static" / "vendor" / "lucide"
_ICON_NAME = re.compile(r"^[a-z0-9-]{1,40}$")
_SVG_BODY = re.compile(r"<svg[^>]*>(.*)</svg>", re.DOTALL)


@lru_cache(maxsize=256)
def _icon_body(name: str) -> str:
    if not _ICON_NAME.match(name):
        return ""
    path = ICON_DIR / f"{name}.svg"
    if not path.is_file():
        return ""
    match = _SVG_BODY.search(path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else ""


def icon(name: str, cls: str = "size-4", label: str | None = None) -> Markup:
    """SVG inline (compatível com CSP). Ícones decorativos ficam ocultos de leitores de tela."""
    body = _icon_body(name) or _icon_body("circle-alert")
    aria = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true"'
    return Markup(  # noqa: S704 - conteúdo vem de arquivos locais versionados e é validado
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'class="{escape(cls)}" {aria} focusable="false">{body}</svg>'
    )


def _brl(cents: int | None, signed: bool = False) -> str:
    return "—" if cents is None else format_brl(int(cents), signed=signed)


def _date_br(value: date | datetime | None) -> str:
    return format_date_br(value)


def _month_label(value: Month | date) -> str:
    if isinstance(value, date):
        value = Month.of(value)
    return value.label()


def _tojson_script(value: Any) -> Markup:
    """JSON seguro para <script type="application/json"> (escapa <, >, &)."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Markup(text)  # noqa: S704 - caracteres perigosos escapados acima


def width_class(value: float) -> str:
    """Classe CSS de largura percentual (0–100); evita style inline bloqueado pela CSP."""
    return f"w-pct-{max(0, min(100, round(value)))}"


def color_class(hex_color: str | None, kind: str = "bg") -> str:
    """Classe CSS da cor da paleta (ex.: bg-c-2563eb, text-c-2563eb, bg-c-2563eb-soft)."""
    value = (hex_color or "#64748b").lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", value):
        value = "64748b"
    if kind == "soft":
        return f"bg-c-{value}-soft"
    return f"{kind}-c-{value}"


def _pct(part: int, whole: int) -> str:
    return f"{percent(part, whole)}".replace(".", ",") + "%"


def build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    env = templates.env
    env.autoescape = True
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.filters.update(
        brl=_brl,
        brl_input=format_input_brl,
        date_br=_date_br,
        month_label=_month_label,
        tojson_script=_tojson_script,
        pct=_pct,
    )
    env.globals.update(
        icon=icon,
        width_class=width_class,
        color_class=color_class,
        TYPE_LABELS=TYPE_LABELS,
        STATUS_LABELS=STATUS_LABELS,
        ORIGIN_LABELS=ORIGIN_LABELS,
        MONTH_NAMES=MONTH_NAMES,
    )
    return templates


templates = build_templates()


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Any:
    return templates.TemplateResponse(
        request, name, context or {}, status_code=status_code, headers=headers
    )
