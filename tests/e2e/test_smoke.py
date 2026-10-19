"""Teste ponta a ponta com navegador real (Playwright) contra a aplicação em execução.

Executar com a stack de desenvolvimento no ar (app + Mailpit):
    E2E_BASE_URL=http://localhost:8000 MAILPIT_URL=http://localhost:8025 uv run pytest tests/e2e -m e2e
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import uuid

import pytest

BASE_URL = os.environ.get("E2E_BASE_URL")
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not BASE_URL, reason="defina E2E_BASE_URL para executar os testes ponta a ponta"
    ),
]


def _link_from_mail(email: str, path: str, timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with urllib.request.urlopen(
            f"{MAILPIT_URL}/api/v1/search?query=to:{email}", timeout=5
        ) as resp:
            messages = json.loads(resp.read())["messages"]
        if messages:
            with urllib.request.urlopen(
                f"{MAILPIT_URL}/api/v1/message/{messages[0]['ID']}", timeout=5
            ) as resp:
                body = json.loads(resp.read())["Text"]
            match = re.search(r"(https?://\S+" + re.escape(path) + r"\?token=[A-Za-z0-9_-]+)", body)
            if match:
                return match.group(1)
        time.sleep(0.5)
    raise AssertionError(f"e-mail com {path} não chegou ao Mailpit")


def test_register_verify_login_and_register_expense() -> None:
    from playwright.sync_api import expect, sync_playwright

    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    password = "Senha-E2E-" + uuid.uuid4().hex[:8]
    console_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="pt-BR")
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))
        page.on(
            "console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None
        )

        page.goto(f"{BASE_URL}/cadastro")
        page.fill("#f-full_name", "Teste Ponta a Ponta")
        page.fill("#f-email", email)
        page.fill("#f-password", password)
        page.fill("#f-password_confirm", password)
        page.check("input[name=accept_privacy]")
        page.click("button[type=submit]")
        expect(page.get_by_text("Verifique sua caixa de entrada")).to_be_visible()

        link = _link_from_mail(email, "/verificar-email").replace(
            "http://localhost:8000", BASE_URL or ""
        )
        page.goto(link)
        page.click("button[type=submit]")
        expect(page.get_by_text("E-mail confirmado")).to_be_visible()

        page.fill("#f-email", email)
        page.fill("#f-password", password)
        page.click("button[type=submit]")
        page.wait_for_url("**/app")
        expect(page.get_by_text("Seu painel ainda está vazio")).to_be_visible()

        # Nova conta pelo drawer (HTMX)
        page.goto(f"{BASE_URL}/app/contas")
        page.get_by_role("link", name=re.compile("Nova conta")).first.click()
        expect(page.locator("#drawer[open]")).to_be_visible()
        page.fill("#drawer #f-name", "Conta E2E")
        page.fill("#drawer #f-initial_balance", "1.000,00")
        page.click("#drawer button[type=submit]")
        expect(page.locator("#drawer[open]")).to_have_count(0)
        expect(page.get_by_text("R$ 1.000,00").first).to_be_visible()

        # Nova despesa
        page.goto(f"{BASE_URL}/app/transacoes")
        page.get_by_role("link", name=re.compile("Novo lançamento")).first.click()
        expect(page.locator("#drawer[open]")).to_be_visible()
        page.fill("#drawer #f-description", "Mercado E2E")
        page.fill("#drawer #f-amount", "123,45")
        account_value = page.locator(
            "#drawer #f-account_id option", has_text="Conta E2E"
        ).get_attribute("value")
        page.select_option("#drawer #f-account_id", value=account_value)
        page.select_option("#drawer #f-expense_category_id", label="Alimentação")
        page.click("#drawer button[type=submit]")
        expect(page.get_by_text("Mercado E2E")).to_be_visible()

        page.goto(f"{BASE_URL}/app")
        expect(page.get_by_text("R$ 876,55").first).to_be_visible()
        browser.close()
    assert not console_errors, console_errors


def test_password_reset_via_smtp_revokes_session_and_token_is_single_use() -> None:
    from playwright.sync_api import expect, sync_playwright

    email = f"e2e-reset-{uuid.uuid4().hex[:8]}@example.com"
    old_password = "Senha-Antiga-" + uuid.uuid4().hex[:8]
    new_password = "Senha-Nova-" + uuid.uuid4().hex[:8]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="pt-BR")
        page.goto(f"{BASE_URL}/cadastro")
        page.fill("#f-full_name", "Teste Recuperação")
        page.fill("#f-email", email)
        page.fill("#f-password", old_password)
        page.fill("#f-password_confirm", old_password)
        page.check("input[name=accept_privacy]")
        page.click("button[type=submit]")
        page.goto(_link_from_mail(email, "/verificar-email"))
        page.click("button[type=submit]")

        page.goto(f"{BASE_URL}/recuperar-senha")
        page.fill("#f-email", email)
        page.click("button[type=submit]")
        expect(page.get_by_text("Se houver uma conta com esse e-mail")).to_be_visible()
        reset_link = _link_from_mail_newest(email, "/redefinir-senha")
        page.goto(reset_link)
        page.fill("#f-password", new_password)
        page.fill("#f-password_confirm", new_password)
        page.click("button[type=submit]")
        expect(page.get_by_text("Senha redefinida")).to_be_visible()

        page.goto(reset_link)  # token de uso único
        expect(page.get_by_text("Link inválido")).to_be_visible()

        page.goto(f"{BASE_URL}/login")
        page.fill("#f-email", email)
        page.fill("#f-password", old_password)
        page.click("button[type=submit]")
        expect(page.get_by_text("E-mail ou senha inválidos.")).to_be_visible()
        page.fill("#f-password", new_password)
        page.click("button[type=submit]")
        page.wait_for_url("**/app")
        browser.close()


def _link_from_mail_newest(email: str, path: str, timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with urllib.request.urlopen(
            f"{MAILPIT_URL}/api/v1/search?query=to:{email}", timeout=5
        ) as resp:
            messages = json.loads(resp.read())["messages"]
        for message in messages:  # mais recente primeiro
            with urllib.request.urlopen(
                f"{MAILPIT_URL}/api/v1/message/{message['ID']}", timeout=5
            ) as resp:
                body = json.loads(resp.read())["Text"]
            match = re.search(r"(https?://\S+" + re.escape(path) + r"\?token=[A-Za-z0-9_-]+)", body)
            if match:
                return match.group(1)
        time.sleep(0.5)
    raise AssertionError(f"e-mail com {path} não chegou ao Mailpit")
