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
- [ ] Estrutura de pastas, `pyproject.toml`, Ruff, mypy, pytest
- [ ] Config validada por ambiente (`development`/`testing`/`production`)
- [ ] Logging JSON com redação de segredos + request id
- [ ] Banco, Base de modelos, Alembic
- [ ] FastAPI + Jinja2 + headers de segurança + handlers globais de erro
- [ ] Assets locais (Tailwind standalone, HTMX, Chart.js, Lucide, Inter)
- [ ] Docker / Compose (web, migrate, postgres, db-test, redis, mailpit, scheduler)
- [ ] Testes iniciais (health, config, money)

### Fase 2 — Segurança e autenticação
- [ ] Cadastro + aceite de política + categorias padrão
- [ ] Verificação de e-mail (token com hash, expiração, uso único)
- [ ] Login / sessão / rotação / logout / timeout ocioso e absoluto
- [ ] Recuperação de senha (invalida sessões)
- [ ] CSRF (forms + HTMX + API) e verificação de Origin
- [ ] Rate limiting (Redis / memória em testes) → 429
- [ ] Gerenciamento da conta: nome, e-mail (re-verificação), senha, encerrar sessões, exportar dados, excluir conta
- [ ] Testes de segurança

### Fase 3 — Núcleo financeiro
- [ ] Contas (RN001–RN010)
- [ ] Categorias (unicidade, arquivamento)
- [ ] Transações (RN011–RN020), estorno, edição consistente, histórico
- [ ] Transferências (RN021–RN028)
- [ ] Auditoria
- [ ] Idempotência
- [ ] Testes de concorrência

### Fase 4 — Dashboard e orçamentos
- [ ] Orçamentos (RN029–RN037) + alertas 80%/100%
- [ ] AnalyticsService (competência × caixa × previsto)
- [ ] Dashboard: KPIs, 5 gráficos, filtros HTMX, estados vazios, skeletons
- [ ] Testes de agregação

### Fase 5 — Complementares
- [ ] Metas (RN038–RN043)
- [ ] Recorrências + job (RN044–RN050)
- [ ] Importação CSV (RN051–RN060)
- [ ] Relatórios + CSV/PDF/impressão
- [ ] Previsão de fluxo de caixa (RN071–RN077)

### Fase 6 — Cartões
- [ ] Cartões, compras, parcelas, faturas, pagamentos, estornos, limites (RN061–RN070)
- [ ] Testes de virada de mês/ano, pagamento parcial, estorno

### Fase 7 — Qualidade e publicação
- [ ] Revisão visual, responsividade, acessibilidade
- [ ] Revisão de segurança
- [ ] Suíte completa + cobertura ≥ 80% em services/domain
- [ ] CI (GitHub Actions)
- [ ] Seed de demonstração
- [ ] Documentação final (README + docs/*) e POST_LINKEDIN.md
- [ ] Screenshots reais

## 3. Registro de execução

| Data | Etapa | Resultado |
|------|-------|-----------|
| 2026-10-16 | Inspeção do ambiente, git init, estrutura | ok |
| 2026-10-16 | `pyproject.toml` + `uv sync` (FastAPI 0.143, Starlette 1.8, SQLAlchemy 2.1, Pydantic 2.14, psycopg 3.3) | ok |
| 2026-10-16 | `app/core/{config,logging,database}.py`; `app/domain/{money,dates,installments,recurrence,ledger}.py` | escritos, **ainda não testados** |
| 2026-10-16 | `app/models/{base,user,finance,planning}.py` | escritos, **ainda não testados** |

### ▶ Ponto de retomada (pausa solicitada pelo usuário)

Próximos passos, nesta ordem:
1. `app/models/imports.py` (ImportBatch, ImportRow), `app/models/cards.py` (CreditCard, CardStatement, CardPurchase, CardInstallment, CardPayment), `app/models/audit.py` (AuditLog) e `app/models/__init__.py` importando todos.
2. Alembic (`alembic.ini`, `env.py`) + migration inicial (extensão `pg_trgm`, trigger append-only da auditoria, roles `finpilot_app`/`finpilot_migrator`).
3. `docker-compose.yml` (postgres, db-test, redis, mailpit) e `.env.example`; subir banco e aplicar migration.
4. Testes unitários do domínio (money, installments, recurrence, dates).
5. `app/main.py`, middlewares (request id, headers de segurança), handlers de erro, Jinja2, assets → fim da Fase 1.

## 4. Limitações conhecidas / bloqueios

- (preenchido conforme ocorrerem)
