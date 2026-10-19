# Testes

## Como executar

```bash
docker compose up -d db-test          # PostgreSQL efêmero (tmpfs) na porta 5433
uv run pytest                         # suíte completa
uv run pytest tests/unit -q           # só regras puras (sem banco)
uv run pytest -m concurrency          # só concorrência
uv run pytest --cov=app/services --cov=app/domain --cov-fail-under=80
# ponta a ponta (navegador real) com a stack rodando (make up):
E2E_BASE_URL=http://localhost:8000 MAILPIT_URL=http://localhost:8025 uv run pytest tests/e2e -m e2e
```

`TEST_DATABASE_URL` aponta para outro PostgreSQL, se necessário (padrão: `127.0.0.1:5433`).

## Estratégia

* **Banco real.** Todos os testes de integração usam PostgreSQL — `FOR UPDATE`, índices parciais, triggers e advisory locks não podem ser simulados em SQLite. O esquema é criado pelas **migrations do Alembic** a cada execução (as migrations também são testadas; o CI executa `upgrade → downgrade → upgrade` num banco vazio).
* **Isolamento.** Cada teste roda numa transação externa desfeita ao final; os `commit()` dos serviços viram SAVEPOINTs. Testes de concorrência usam conexões reais com commit e limpam as tabelas ao final.
* **Dados.** Factories em `tests/factories.py` criam dados pelos próprios serviços (sem inserção direta), então os testes exercitam as mesmas regras de produção. Nenhum dado real.
* **Determinismo.** Relógio injetável (`FrozenClock`), rate limiter e e-mail em memória, testes de propriedade com Hypothesis para dinheiro, parcelamento e recorrências.

## Suítes

| Pasta | Conteúdo | Testes |
|---|---|---|
| `tests/unit` | dinheiro (parsing/formatação/arredondamento), parcelas e calendário de faturas, recorrências e fuso, validação de configuração | 67 |
| `tests/integration` | fluxos de autenticação e conta, regras do núcleo financeiro, orçamentos/analytics/previsão, metas, recorrências, importação, relatórios/exportações, cartões, API REST, telas HTMX, logging | 141 |
| `tests/security` | CSRF (inclusive token lido do HTML), rate limiting, expiração de sessões e tokens, cabeçalhos, open redirect, IDOR/BOLA em web e API, mass assignment, SQL injection, XSS, varredura de rotas protegidas | 39 |
| `tests/concurrency` | despesas simultâneas, transferências opostas, chave de idempotência repetida, confirmação duplicada de importação, gerador de recorrências simultâneo, pagamento duplicado de fatura, compras simultâneas contra o limite | 8 |
| `tests/e2e` | Playwright: (1) cadastro → verificação via Mailpit → login → conta → despesa → dashboard, sem erros de console; (2) recuperação de senha por SMTP real, token de uso único e senha antiga invalidada | 2 |

## Resultado da última execução local

Executado em 2026-10-19 (Windows 11, Python 3.12.7, PostgreSQL 16 em Docker):

* `uv run pytest` → **255 passed, 2 skipped** (os e2e são ignorados sem `E2E_BASE_URL`).
* `pytest tests/e2e -m e2e` contra a stack Docker → **2 passed** (executado duas vezes seguidas; os contadores de rate limit do Redis de desenvolvimento são zerados antes de cada teste).
* Varredura automática de 308 páginas, links e exportações autenticados do usuário demo: todas responderam 200.
* Cobertura (linhas): `app/services` **90,6%**, `app/domain` **96,1%**, total `app` **85,0%**.
* `ruff check` e `ruff format --check`: sem pendências · `mypy --strict`: sem erros (89 arquivos).

Esses números foram obtidos localmente; o pipeline de CI (`.github/workflows/ci.yml`) executa as mesmas etapas a cada push e pull request.
