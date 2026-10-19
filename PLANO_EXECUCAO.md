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
- [~] Docker / Compose — infraestrutura (postgres, db-test, redis, mailpit) validada; imagem da aplicação pendente (Fase 7)
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
- [ ] Metas (RN038–RN043)
- [ ] Recorrências + job (RN044–RN050)
- [ ] Importação CSV (RN051–RN060)
- [ ] Relatórios + CSV/PDF/impressão
- [x] Previsão de fluxo de caixa (RN071–RN077)

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
| 2026-10-19 | Fases 1–4 (fundação, auth, núcleo financeiro, dashboard/orçamentos/previsão) | 171 testes ok |

### ▶ Ponto de retomada

Concluído e testado até a Fase 4 (MVP) — 171 testes passando (`uv run pytest`), screenshots reais conferidas.
Próximos passos, nesta ordem:
1. API REST v1 (`app/api/v1/`) reutilizando os mesmos serviços (seção 21).
2. Fase 5: metas, recorrências + job, importação CSV, relatórios CSV/PDF.
3. Fase 6: cartões (o modelo e as consultas `card_queries.py` já existem; dashboard/previsão já consideram faturas).
4. Fase 7: Dockerfile, CI, docs, revisão de segurança, cobertura, seed completo.

Como rodar localmente: `docker compose up -d postgres db-test redis mailpit`, `uv run alembic upgrade head`,
`uv run python -m scripts.seed_demo`, `uv run uvicorn app.main:app --reload`.

## 4. Limitações conhecidas / bloqueios

- (preenchido conforme ocorrerem)
