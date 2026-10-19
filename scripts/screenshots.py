"""Captura screenshots REAIS da aplicação em execução (usado no README).

Pré-requisitos: aplicação rodando (ex.: `make run`) e seed de demonstração aplicado.
Uso: python -m scripts.screenshots --base-url http://localhost:8000 --out docs/screenshots
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.utils.env import load_dotenv_if_present

PAGES = [
    ("dashboard", "/app"),
    ("transacoes", "/app/transacoes"),
    ("contas", "/app/contas"),
    ("orcamentos", "/app/orcamentos"),
    ("previsao", "/app/previsao?dias=90"),
    ("categorias", "/app/categorias"),
    ("transferencias", "/app/transferencias"),
    ("configuracoes", "/app/configuracoes"),
]
EXTRA_PAGES = [
    ("cartoes", "/app/cartoes"),
    ("metas", "/app/metas"),
    ("recorrencias", "/app/recorrencias"),
    ("relatorios", "/app/relatorios"),
    ("importacao", "/app/importacoes"),
]


def main() -> None:
    load_dotenv_if_present()
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default="docs/screenshots")
    parser.add_argument("--theme", default="light", choices=["light", "dark"])
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--mobile", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    email, password = os.environ["DEMO_EMAIL"], os.environ["DEMO_PASSWORD"]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        viewport = {"width": 390, "height": 844} if args.mobile else {"width": 1440, "height": 900}
        context = browser.new_context(
            viewport=viewport, device_scale_factor=2 if args.mobile else 1, locale="pt-BR"
        )
        context.add_cookies([{"name": "fp_theme", "value": args.theme, "url": args.base_url}])
        page = context.new_page()
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"{args.base_url}/login")
        page.screenshot(path=str(out / f"login-{args.theme}.png"))
        page.fill("#f-email", email)
        page.fill("#f-password", password)
        page.click("button[type=submit]")
        page.wait_for_url("**/app")
        for name, path in PAGES + EXTRA_PAGES:
            if args.only and name not in args.only:
                continue
            response = page.goto(args.base_url + path)
            if response is None or response.status != 200:
                print(f"pulando {path}: status {response.status if response else '—'}")
                continue
            page.wait_for_timeout(700)
            suffix = "-mobile" if args.mobile else ""
            page.screenshot(
                path=str(out / f"{name}-{args.theme}{suffix}.png"), full_page=not args.mobile
            )
            print(f"ok {name}")
        browser.close()
    if errors:
        print("ERROS NO CONSOLE:")
        for e in errors:
            print(" -", e)


if __name__ == "__main__":
    main()
