"""Grava um vídeo de demonstração REAL do FinPilot (navegador controlado pelo Playwright).

Pré-requisitos: aplicação rodando com o seed de demonstração (`make up` + `make seed-reset`).
Uso:
    uv run python -m scripts.demo_video --base-url http://localhost:8000 --out docs/video

Gera `finpilot-demo.webm`. A conversão para MP4 é feita à parte (ver docs/POST_LINKEDIN.md).
Os dados de demonstração são alterados durante a gravação; rode `make seed-reset` depois.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

from app.utils.env import load_dotenv_if_present

# Legenda e cursor visíveis no vídeo (o Playwright não grava o ponteiro do sistema).
OVERLAY_JS = """
(() => {
  const install = () => {
    if (document.getElementById('__demo_cursor')) return;
    const cursor = document.createElement('div');
    cursor.id = '__demo_cursor';
    Object.assign(cursor.style, {
      position: 'fixed', left: '0px', top: '0px', width: '18px', height: '18px', borderRadius: '50%',
      background: 'rgba(23,23,23,.35)', border: '2px solid rgba(255,255,255,.9)', boxShadow: '0 0 0 1px rgba(0,0,0,.35)',
      zIndex: 2147483647, pointerEvents: 'none', transform: 'translate(-50%,-50%)', transition: 'width .12s, height .12s',
    });
    document.body.appendChild(cursor);
    window.addEventListener('mousemove', (e) => { cursor.style.left = e.clientX + 'px'; cursor.style.top = e.clientY + 'px'; }, true);
    window.addEventListener('mousedown', () => { cursor.style.width = '12px'; cursor.style.height = '12px'; }, true);
    window.addEventListener('mouseup', () => { cursor.style.width = '18px'; cursor.style.height = '18px'; }, true);
    const cap = document.createElement('div');
    cap.id = '__demo_caption';
    Object.assign(cap.style, {
      position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)', maxWidth: '78vw', width: 'max-content', boxSizing: 'border-box',
      padding: '12px 20px', borderRadius: '10px', background: 'rgba(17,17,17,.88)', color: '#fafafa',
      font: '500 17px/1.45 Inter, system-ui, sans-serif', textAlign: 'center', zIndex: 2147483646,
      pointerEvents: 'none', opacity: '0', transition: 'opacity .25s', margin: '0', border: '0',
      top: 'auto', right: 'auto',
    });
    // Popover fica na "top layer": aparece acima de <dialog> modal/drawer abertos.
    cap.setAttribute('popover', 'manual');
    document.body.appendChild(cap);
    const raise = () => { try { if (cap.matches(':popover-open')) cap.hidePopover(); cap.showPopover(); } catch (e) {} };
    window.__raiseCaption = raise;
    raise();
    document.addEventListener('htmx:afterSettle', () => setTimeout(raise, 50));
    document.addEventListener('click', () => setTimeout(raise, 80), true);
    const saved = sessionStorage.getItem('__demo_caption');
    if (saved) { cap.textContent = saved; cap.style.opacity = '1'; }
  };
  window.__caption = (text) => {
    sessionStorage.setItem('__demo_caption', text || '');
    const cap = document.getElementById('__demo_caption');
    if (!cap) return;
    cap.textContent = text || '';
    cap.style.opacity = text ? '1' : '0';
    if (window.__raiseCaption) window.__raiseCaption();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
"""

CSV_SAMPLE = (
    "Data;Descrição;Valor;Documento\n"
    "02/10/2026;Pix recebido - Cliente Silva;1.250,00;BX9001\n"
    "03/10/2026;Padaria Pão Quente;-38,90;BX9002\n"
    "04/10/2026;Posto Shell;-210,00;BX9003\n"
    "31/02/2026;Lançamento com data inválida;-10,00;BX9004\n"
    "06/10/2026;Farmácia Popular;-64,35;BX9005\n"
)


class Demo:
    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base = base_url.rstrip("/")

    def wait(self, ms: int = 900) -> None:
        self.page.wait_for_timeout(ms)

    def caption(self, text: str, hold: int = 1600) -> None:
        self.page.evaluate("t => window.__caption && window.__caption(t)", text)
        self.wait(hold)

    def goto(self, path: str) -> None:
        self.page.goto(self.base + path)
        self.page.wait_for_load_state("networkidle")
        self.wait(500)

    def move_to(self, locator: Locator) -> None:
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box:
            self.page.mouse.move(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=18
            )
        self.wait(250)

    def click(self, locator: Locator, pause: int = 700) -> None:
        self.move_to(locator)
        locator.click()
        self.wait(pause)

    def type(self, locator: Locator, text: str) -> None:
        self.move_to(locator)
        locator.click()
        locator.fill("")
        locator.press_sequentially(text, delay=70)
        self.wait(300)

    def select_containing(self, select: Locator, text: str) -> None:
        self.move_to(select)
        value = select.locator("option", has_text=text).first.get_attribute("value")
        select.select_option(value=value)
        self.wait(500)

    def scroll(self, pixels: int, steps: int = 6) -> None:
        for _ in range(steps):
            self.page.mouse.wheel(0, pixels / steps)
            self.wait(120)
        self.wait(500)

    def drawer(self) -> Locator:
        drawer = self.page.locator("#drawer[open]")
        drawer.wait_for()
        self.wait(500)
        return drawer

    def submit_drawer(self) -> None:
        self.click(self.page.locator("#drawer button[type=submit]"), pause=300)
        self.page.locator("#drawer[open]").wait_for(state="detached")
        self.wait(1300)


def run(demo: Demo, email: str, password: str, csv_path: Path) -> None:  # noqa: PLR0915 - roteiro linear
    p = demo.page

    # 1. Apresentação e login
    demo.goto("/")
    demo.caption(
        "FinPilot — gestão financeira pessoal em Python (FastAPI + PostgreSQL + HTMX)", 2600
    )
    demo.scroll(500)
    demo.caption(
        "Contas, cartões, orçamentos, metas, recorrências, importação CSV e previsão de caixa", 2200
    )
    demo.goto("/login")
    demo.caption("Login com sessão segura (cookie HttpOnly, CSRF, rate limiting)", 1200)
    demo.type(p.locator("#f-email"), email)
    demo.type(p.locator("#f-password"), password)
    demo.click(p.locator("button[type=submit]"), pause=300)
    p.wait_for_url("**/app")
    demo.wait(1200)

    # 2. Dashboard
    demo.caption(
        "Dashboard: indicadores com o regime explícito — competência, caixa ou projetado", 2600
    )
    demo.scroll(700, steps=8)
    demo.caption(
        "Receitas × despesas de 12 meses, gastos por categoria e evolução do saldo (dados reais do banco)",
        2600,
    )
    demo.scroll(700, steps=8)
    demo.caption("Orçamento por categoria e fluxo de caixa projetado para 90 dias", 2400)
    demo.scroll(-1400, steps=8)
    demo.caption("Filtros atualizam o painel via HTMX, sem recarregar a página", 1400)
    demo.select_containing(p.locator("#d-acc"), "Nubank")
    p.wait_for_load_state("networkidle")
    demo.wait(1600)
    demo.select_containing(p.locator("#d-acc"), "Todas")
    p.wait_for_load_state("networkidle")
    demo.wait(1000)

    # 3. Nova despesa
    demo.goto("/app/transacoes")
    demo.caption("Transações: filtros, busca, totalizadores e paginação feitos no banco", 2200)
    demo.click(p.get_by_role("link", name="Novo lançamento").first)
    drawer = demo.drawer()
    demo.caption(
        "Novo lançamento em um painel lateral — validação no backend, valores em pt-BR", 1800
    )
    demo.type(drawer.locator("#f-description"), "Supermercado do mês")
    demo.type(drawer.locator("#f-amount"), "487,90")
    demo.select_containing(drawer.locator("#f-account_id"), "Nubank")
    demo.select_containing(drawer.locator("#f-expense_category_id"), "Alimentação")
    demo.caption("Chave de idempotência: um duplo clique nunca lança a despesa duas vezes", 1600)
    demo.submit_drawer()
    demo.caption("Saldo atualizado na hora, dentro de uma transação ACID com lock da conta", 2200)
    demo.type(p.locator("#flt-q"), "Supermercado")
    p.keyboard.press("Enter")
    p.wait_for_load_state("networkidle")
    demo.wait(1800)

    # 4. Estorno com histórico
    menu = p.locator("[data-menu-trigger][aria-label*='Supermercado do mês']").first
    demo.click(menu)
    demo.click(p.locator("[data-menu]:not([hidden]) a", has_text="Histórico").first)
    p.locator("#modal[open]").wait_for()
    demo.caption("Cada alteração fica na trilha de auditoria imutável", 2200)
    p.keyboard.press("Escape")
    demo.wait(600)

    # 5. Transferência
    demo.click(p.get_by_role("link", name="Transferir").first)
    drawer = demo.drawer()
    demo.caption("Transferência entre contas: atômica e fora das receitas e despesas", 1800)
    demo.select_containing(drawer.locator("#f-from_account_id"), "Nubank")
    demo.select_containing(drawer.locator("#f-to_account_id"), "Poupança")
    demo.type(drawer.locator("#f-amount"), "300,00")
    demo.type(drawer.locator("#f-description"), "Reserva extra")
    demo.submit_drawer()

    # 6. Cartão parcelado
    demo.goto("/app/cartoes")
    demo.caption(
        "Cartões: limite utilizado, fatura atual e valores a pagar — limite não é saldo bancário",
        2400,
    )
    demo.click(p.get_by_role("link", name="Nova compra").first)
    drawer = demo.drawer()
    demo.type(drawer.locator("#f-description"), "Notebook")
    demo.select_containing(drawer.locator("#f-category_id"), "Compras")
    demo.type(drawer.locator("#f-amount"), "1.000,00")
    demo.type(drawer.locator("#f-installments"), "3")
    p.keyboard.press("Tab")
    demo.wait(1200)
    demo.caption(
        "Parcelas exatas ao centavo: 333,34 + 333,33 + 333,33, cada uma na fatura certa", 2800
    )
    demo.submit_drawer()
    demo.click(p.get_by_role("link", name="Faturas e compras").first)
    demo.caption(
        "Faturas por fechamento e vencimento; pagamento é saída de caixa, sem contar a despesa duas vezes",
        2800,
    )

    # 7. Orçamentos e metas
    demo.goto("/app/orcamentos")
    demo.caption("Orçamentos com alertas em 80% e 100% — previsto separado do realizado", 2600)
    demo.scroll(400)
    demo.goto("/app/metas")
    demo.caption("Metas com reservas virtuais: não reduzem o saldo das contas", 2000)
    demo.click(p.locator("a.card").first)
    demo.click(p.get_by_role("link", name="Reservar").first)
    drawer = demo.drawer()
    demo.type(drawer.locator("#f-amount"), "500,00")
    demo.submit_drawer()
    demo.caption("Progresso, estimativa de conclusão e quanto reservar por mês até o prazo", 2400)

    # 8. Recorrências
    demo.goto("/app/recorrencias")
    demo.caption("Recorrências geram lançamentos previstos sem duplicar (job idempotente)", 2400)

    # 9. Importação CSV
    demo.goto("/app/importacoes")
    demo.caption("Importação de extrato CSV: nada é lançado antes da sua confirmação", 2000)
    demo.select_containing(p.locator("#imp-account"), "Nubank")
    p.locator("#imp-file").set_input_files(str(csv_path))
    demo.wait(600)
    demo.click(p.get_by_role("button", name="Enviar e mapear"), pause=300)
    p.wait_for_load_state("networkidle")
    demo.caption("O sistema detecta delimitador, codificação e colunas automaticamente", 2000)
    demo.click(p.get_by_role("button", name="Gerar prévia"), pause=300)
    p.wait_for_load_state("networkidle")
    demo.wait(600)
    p.locator("#previa").scroll_into_view_if_needed()
    demo.caption(
        "Prévia validada: linhas inválidas com o motivo e duplicatas marcadas para revisão", 2800
    )
    demo.click(p.get_by_role("button", name="Confirmar importação"), pause=400)
    demo.click(p.locator("#confirm-dialog [data-confirm-ok]"), pause=300)
    p.wait_for_load_state("networkidle")
    demo.caption("Relatório da importação: importadas, ignoradas e falhas", 2200)

    # 10. Previsão e relatórios
    demo.goto("/app/previsao?dias=90")
    demo.caption("Previsão determinística de 30, 60 e 90 dias, com data de saldo negativo", 2600)
    demo.scroll(500)
    demo.goto("/app/relatorios")
    demo.caption("8 relatórios com exportação em CSV, PDF e impressão", 1800)
    demo.click(p.get_by_role("link", name="Gastos por categoria"))
    demo.wait(1400)

    # 11. Tema escuro e encerramento
    demo.goto("/app")
    demo.caption("Modo claro e escuro", 1000)
    demo.click(p.locator("[aria-controls=theme-menu]"))
    demo.click(p.locator("#theme-menu button", has_text="Escuro"), pause=300)
    p.wait_for_load_state("networkidle")
    demo.wait(1800)
    demo.scroll(700, steps=8)
    demo.caption("FinPilot · 257 testes · segurança OWASP · Docker · CI", 3200)
    demo.click(p.locator("[aria-controls=theme-menu]"))
    demo.click(p.locator("#theme-menu button", has_text="Claro"), pause=300)
    demo.caption("", 600)


def main() -> None:
    load_dotenv_if_present()
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--out", default="docs/video")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    csv_path = tmp / "extrato-outubro.csv"
    csv_path.write_text(CSV_SAMPLE, encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--lang=pt-BR"])
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(tmp),
            record_video_size={"width": 1280, "height": 800},
            locale="pt-BR",
            bypass_csp=True,  # apenas para injetar legenda e cursor na gravação
        )
        context.add_cookies([{"name": "fp_theme", "value": "light", "url": args.base_url}])
        context.add_init_script(OVERLAY_JS)
        page = context.new_page()
        try:
            run(
                Demo(page, args.base_url),
                os.environ["DEMO_EMAIL"],
                os.environ["DEMO_PASSWORD"],
                csv_path,
            )
        finally:
            video = page.video
            context.close()
            browser.close()
        if video:
            target = out / "finpilot-demo.webm"
            shutil.move(video.path(), target)
            print(f"Vídeo salvo em {target}")


if __name__ == "__main__":
    main()
