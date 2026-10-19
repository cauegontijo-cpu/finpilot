# PLANO DE EXECUÇÃO — FinPilot

Documento vivo. Atualizado a cada etapa para permitir retomada segura caso a sessão seja interrompida.
Legenda: `[x]` concluído e validado por teste/execução · `[~]` parcial · `[ ]` pendente · `[!]` bloqueado (ver nota).

## 0. Ambiente inspecionado (2026-10-16)

- Diretório inicial vazio — sem projeto prévio.
- Windows 11, Python 3.12.7, uv 0.10, Node 24 (não utilizado), Git 2.53, Docker 29.4 + Compose v5.
- PostgreSQL não instalado localmente → executado via Docker Compose.
- Projeto dentro do OneDrive: `.venv`, caches e volumes podem sofrer com sincronização (documentado no README).

## 1. Decisões técnicas principais (detalhes em `docs/adr/`)

| # | Decisão |
|---|---------|
| ADR-001 | Monólito modular FastAPI; camadas `web`/`api` → `services` → `repositories`/`models`; regras puras em `app/domain`. |
| ADR-002 | SQLAlchemy 2 **síncrono** + psycopg 3; endpoints `def` (threadpool). Simplicidade e locks `FOR UPDATE` previsíveis. |
| ADR-003 | Dinheiro em centavos `BIGINT`; `Decimal` nas conversões; `ROUND_HALF_EVEN`; sem float. |
| ADR-004 | Sessões server-side (hash do token no banco), cookie HttpOnly/SameSite=Lax/Secure em produção; CSRF HMAC ligado à sessão. |
| ADR-005 | Saldo atual desnormalizado, alterado só pelo `LedgerService` sob `SELECT … FOR UPDATE` (ordem por id) + reconciliação. |
| ADR-006 | Idempotência via tabela `idempotency_keys` (unique `user_id,key`) na mesma transação da operação. |
| ADR-007 | Cartões: regra de fechamento/vencimento, distribuição de centavos, pagamento como `card_payment` (caixa, não despesa). |
| ADR-008 | Recorrências: âncora + último dia válido do mês; unique `(recurrence_id, occurrence_date)`; advisory lock. |
| ADR-009 | Auditoria append-only (grants + trigger), retenção configurável. |
| ADR-010 | Frontend SSR Jinja2 + HTMX, Tailwind v4 standalone (sem Node), CSP estrita sem inline. |

## 2. Fases e checklist

### Fase 1 — Fundação
- [x] Estrutura de pastas, `pyproject.toml`, Ruff, mypy, pytest
- [x] Config validada por ambiente (`development`/`testing`/`production`)
- [x] Logging JSON com redação de segredos + request id
- [x] Banco, Base de modelos, Alembic
- [x] FastAPI + Jinja2 + headers de segurança + handlers globais de erro
- [x] Assets locais (Tailwind standalone, HTMX, Chart.js, Lucide, Inter)
- [x] Docker / Compose (web, migrate, postgres, db-test, redis, mailpit, scheduler)
- [x] Testes iniciais (health, config, money)

### Fase 2 — Segurança e autenticação
- [x] Cadastro + aceite de política + categorias padrão
- [x] Verificação de e-mail (token com hash, expiração, uso único)
- [x] Login / sessão / rotação / logout / timeout ocioso e absoluto
- [x] Recuperação de senha (invalida sessões)
- [x] CSRF (forms + HTMX + API) e verificação de Origin
- [x] Rate limiting (Redis / memória em testes) → 429
- [x] Gerenciamento da conta: nome, e-mail (re-verificação), senha, encerrar sessões, exportar dados, excluir conta
- [x] Testes de segurança

### Fase 3 — Núcleo financeiro
- [x] Contas (RN001–RN010)
- [x] Categorias (unicidade, arquivamento)
- [x] Transações (RN011–RN020), estorno, edição consistente, histórico
- [x] Transferências (RN021–RN028)
- [x] Auditoria
- [x] Idempotência
- [x] Testes de concorrência

### Fase 4 — Dashboard e orçamentos
- [x] Orçamentos (RN029–RN037) + alertas 80%/100%
- [x] AnalyticsService (competência × caixa × previsto)
- [x] Dashboard: KPIs, 5 gráficos, filtros HTMX, estados vazios, skeletons
- [x] Testes de agregação

### Fase 5 — Complementares
- [x] Metas (RN038–RN043)
- [x] Recorrências + job (RN044–RN050)
- [x] Importação CSV (RN051–RN060)
- [x] Relatórios + CSV/PDF/impressão
- [x] Previsão de fluxo de caixa (RN071–RN077)

### Fase 6 — Cartões
- [x] Cartões, compras, parcelas, faturas, pagamentos, estornos, limites (RN061–RN070)
- [x] Testes de virada de mês/ano, pagamento parcial, estorno

### Fase 7 — Qualidade e publicação
- [x] Revisão visual, responsividade, acessibilidade
- [x] Revisão de segurança
- [x] Suíte completa + cobertura ≥ 80% em services/domain
- [x] CI (GitHub Actions)
- [x] Seed de demonstração
- [x] Documentação final (README + docs/*) e POST_LINKEDIN.md
- [x] Screenshots reais

## 3. Registro de execução

| Data | Etapa | Resultado |
|------|-------|-----------|
| 2026-10-16 | Inspeção do ambiente, git init, estrutura | ok |
| 2026-10-16 | `pyproject.toml` + `uv sync` (FastAPI 0.143, Starlette 1.8, SQLAlchemy 2.1, Pydantic 2.14, psycopg 3.3) | ok |
| 2026-10-16 | `app/core/{config,logging,database}.py`; `app/domain/{money,dates,installments,recurrence,ledger}.py` | escritos, **ainda não testados** |
| 2026-10-16 | `app/models/{base,user,finance,planning}.py` | escritos, **ainda não testados** |
| 2026-10-19 | Fases 1–4 (fundação, auth, núcleo financeiro, dashboard/orçamentos/previsão) | 171 testes ok |

### ▶ Situação final (2026-10-19)

Todas as fases concluídas e validadas: 255 testes + 1 e2e (Playwright) aprovados; ruff e mypy --strict limpos;
imagem Docker validada em modo produção; backup/restore testados; documentação em README.md e docs/.

## 4. Critérios de aceitação final (seção 31) — verificados em 2026-10-19

| Critério | Status | Evidência |
|---|---|---|
| A aplicação inicia corretamente | ✅ | `docker compose up -d` → web `healthy`; `/health/ready` ok (banco + Redis); modo produção validado em container |
| O banco é criado pelas migrations | ✅ | `tests/conftest.py` recria o schema via Alembic a cada execução; upgrade → downgrade → upgrade em banco vazio |
| Usuários criam contas e autenticam | ✅ | `integration/test_auth_flow.py`; e2e `test_register_verify_login_and_register_expense` |
| Recuperação de senha funciona no ambiente configurado | ✅ | e2e `test_password_reset_via_smtp_revokes_session_and_token_is_single_use` (SMTP real → Mailpit) |
| Isolamento de dados testado | ✅ | `security/test_access_control.py` (web), `integration/test_api.py` (API), relatórios, importação e cartões |
| Contas financeiras funcionam | ✅ | `integration/test_ledger_rules.py`, `test_finance_web.py` |
| Receitas e despesas alteram o saldo corretamente | ✅ | `test_settled_income_and_expense_change_balance` + reconciliação do saldo com o histórico |
| Transferências são atômicas | ✅ | `test_transfer_insufficient_funds_rolls_back_both_legs`, concorrência de transferências opostas |
| Estornos mantêm consistência | ✅ | `test_cancel_settled_reverses_and_keeps_history`, `test_transfer_cancel_reverts_both_sides`, estorno de cartão |
| Categorias funcionam | ✅ | `integration/test_categories.py` |
| Orçamentos com cálculos corretos | ✅ | `integration/test_analytics_budgets.py` (80/100%, previsto separado, estorno, parcelas de cartão) |
| Dashboard com dados reais | ✅ | `test_dashboard_renders_with_data_and_charts`; screenshots reais com o seed |
| Filtros do dashboard funcionam | ✅ | parcial HTMX por mês/conta/categoria testado; filtro de conta alheia ignorado/404 |
| Metas funcionam | ✅ | `integration/test_goals_recurrences.py` |
| Recorrências não geram duplicatas | ✅ | `test_generation_is_idempotent`, `concurrency::test_concurrent_recurrence_jobs_never_duplicate` |
| Importação CSV com prévia e validação | ✅ | `integration/test_imports_reports.py` (inclui fluxo web completo e confirmação concorrente) |
| Relatórios funcionam | ✅ | 8 relatórios em HTML/CSV/PDF/impressão testados; exportações percorridas pela varredura |
| Previsão de caixa consistente | ✅ | `test_forecast_*` (saldo realizado, vencimento, transferências, faturas, incerteza) |
| Cartões e parcelamentos funcionam | ✅ | `integration/test_cards.py` (14 testes) + concorrência de pagamento e limite |
| Pagamento de fatura não duplica despesa | ✅ | `test_payment_reduces_cash_not_expense_and_frees_limit` |
| Operações importantes auditadas | ✅ | eventos de login, transação, estorno, transferência, importação, pagamento, orçamento e conta; trigger append-only testado |
| Principais testes automatizados passam | ✅ | 255 passed (+2 e2e passed); ruff e mypy --strict limpos |
| A aplicação é responsiva | ✅ | screenshots mobile (390 px) de dashboard e transações; sidebar em drawer; filtros recolhíveis |
| Docker Compose inicia os serviços | ✅ | postgres, db-test, redis, mailpit, migrate (exit 0), web (healthy), scheduler |
| Documentação suficiente para terceiros | ✅ | README + docs/ (ARCHITECTURE, DATA_MODEL, BUSINESS_RULES, SECURITY, API, DEPLOYMENT, TESTING, ADRs) |
| Sem credenciais reais no repositório | ✅ | `.env` fora do Git; varredura de segredos e de valores do `.env` local no repositório: nenhum encontrado |
| Sem funcionalidades obrigatórias simuladas | ✅ | varredura de 308 páginas/links autenticados sem nenhum erro; único conteúdo ilustrativo (card da landing) é rotulado como tal |

### Itens incompletos (fora do alcance deste ambiente)

- **CI no GitHub Actions:** workflow criado (`.github/workflows/ci.yml`), mas não executado — o repositório ainda não foi publicado. As mesmas etapas foram executadas localmente.
- **Deploy em servidor Linux com HTTPS real:** `docker-compose.prod.yml` + Caddy prontos e a imagem validada em modo produção localmente; falta um servidor e um domínio para o certificado Let's Encrypt.

## 5. Limitações conhecidas / bloqueios

- Tailwind standalone (Bun) falha com `-o` em caminhos não ASCII ("ç") → compilação via stdout.
- Docker Compose (bake) falha ao construir vários serviços em caminho não ASCII → imagem única `finpilot:dev`.
- OneDrive bloqueia arquivos da `.venv` ocasionalmente → recomendado mover o projeto para fora do OneDrive.
- Pipeline do GitHub Actions criado, mas ainda não executado (repositório não publicado no GitHub).
