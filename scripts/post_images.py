"""Gera imagens em alta resolução de todas as telas do FinPilot, prontas para divulgação.

Pré-requisitos: aplicação rodando e seed de demonstração aplicado (`make seed-reset`).
Uso: uv run python -m scripts.post_images --out imagens-post
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from app.utils.env import load_dotenv_if_present

APP_PAGES = [
    ("03-dashboard", "/app"),
    ("04-transacoes", "/app/transacoes"),
    ("05-contas", "/app/contas"),
    ("06-cartoes", "/app/cartoes"),
    ("09-orcamentos", "/app/orcamentos"),
    ("10-metas", "/app/metas"),
    ("12-recorrencias", "/app/recorrencias"),
    ("13-previsao-de-caixa", "/app/previsao?dias=90"),
    ("14-relatorios", "/app/relatorios"),
    ("15-relatorio-categorias", "/app/relatorios/categorias"),
    ("16-importacao-csv", "/app/importacoes"),
    ("17-categorias", "/app/categorias"),
    ("18-transferencias", "/app/transferencias"),
    ("19-auditoria", "/app/auditoria"),
    ("20-configuracoes", "/app/configuracoes"),
]


def shot(page: Page, out: Path, name: str, *, full: bool = True) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(900)  # animações dos gráficos
    page.screenshot(path=str(out / f"{name}.png"), full_page=full)
    print("ok", name)


def first_href(page: Page, pattern: str) -> str | None:
    for href in page.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
        if href and re.fullmatch(pattern, href):
            return str(href)
    return None


def login(page: Page, base: str, email: str, password: str) -> None:
    page.goto(f"{base}/login")
    page.fill("#f-email", email)
    page.fill("#f-password", password)
    page.click("button[type=submit]")
    page.wait_for_url("**/app")


def main() -> None:
    load_dotenv_if_present()
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--out", default="imagens-post")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    email, password = os.environ["DEMO_EMAIL"], os.environ["DEMO_PASSWORD"]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--lang=pt-BR"])

        # Desktop, tema claro, 2x (nitidez para redes sociais)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2, locale="pt-BR"
        )
        ctx.add_cookies([{"name": "fp_theme", "value": "light", "url": base}])
        page = ctx.new_page()
        page.goto(f"{base}/")
        shot(page, out, "01-pagina-inicial")
        page.goto(f"{base}/login")
        shot(page, out, "02-login", full=False)
        login(page, base, email, password)
        for name, path in APP_PAGES:
            page.goto(base + path)
            shot(page, out, name)
            if name == "06-cartoes":
                card = first_href(page, r"/app/cartoes/[0-9a-f-]{36}")
                if card:
                    page.goto(base + card)
                    shot(page, out, "07-cartao-faturas-e-compras")
                    statement = first_href(page, r"/app/faturas/[0-9a-f-]{36}")
                    if statement:
                        page.goto(base + statement)
                        shot(page, out, "08-detalhe-da-fatura")
            if name == "10-metas":
                goal = first_href(page, r"/app/metas/[0-9a-f-]{36}")
                if goal:
                    page.goto(base + goal)
                    shot(page, out, "11-detalhe-da-meta")
            if name == "04-transacoes":
                page.get_by_role("link", name="Novo lançamento").first.click()
                page.locator("#drawer[open]").wait_for()
                page.wait_for_timeout(500)
                shot(page, out, "04b-novo-lancamento", full=False)
        ctx.close()

        # Tema escuro
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900}, device_scale_factor=2, locale="pt-BR"
        )
        ctx.add_cookies([{"name": "fp_theme", "value": "dark", "url": base}])
        page = ctx.new_page()
        login(page, base, email, password)
        for name, path in [
            ("21-dashboard-escuro", "/app"),
            ("22-transacoes-escuro", "/app/transacoes"),
            ("23-cartoes-escuro", "/app/cartoes"),
        ]:
            page.goto(base + path)
            shot(page, out, name)
        ctx.close()

        # Celular
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=3,
            locale="pt-BR",
            is_mobile=True,
            has_touch=True,
        )
        ctx.add_cookies([{"name": "fp_theme", "value": "light", "url": base}])
        page = ctx.new_page()
        login(page, base, email, password)
        for name, path in [
            ("24-celular-dashboard", "/app"),
            ("25-celular-transacoes", "/app/transacoes"),
            ("26-celular-cartoes", "/app/cartoes"),
        ]:
            page.goto(base + path)
            shot(page, out, name, full=False)
        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
