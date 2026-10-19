"""Carregamento mínimo de `.env` para ferramentas de linha de comando (Alembic, scripts).

A aplicação usa pydantic-settings; aqui apenas exportamos as variáveis para
`os.environ` sem sobrescrever valores já definidos no ambiente.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(path: str | Path = ".env") -> None:
    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
