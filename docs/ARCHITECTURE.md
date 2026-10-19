# Arquitetura

FinPilot é um **monólito modular** em FastAPI ([ADR-001](adr/001-monolito-modular.md)). Páginas SSR (Jinja2 + HTMX) e API REST compartilham a mesma camada de serviços — não existe regra financeira implementada duas vezes.

## Visão geral

```mermaid
flowchart LR
  subgraph Cliente
    B[Navegador<br/>HTML + HTMX + Chart.js]
    C[Cliente da API<br/>JSON]
  end
  subgraph Edge
    P[Caddy<br/>HTTPS / HSTS]
  end
  subgraph App[Aplicação FastAPI]
    MW[Middlewares<br/>request id · headers de segurança<br/>limite de corpo · CSRF]
    W[app/web<br/>rotas SSR]
    A[app/api/v1<br/>rotas REST]
    S[app/services<br/>regras + transações]
    D[app/domain<br/>funções puras]
    R[app/repositories + models<br/>SQLAlchemy 2]
  end
  J[Scheduler<br/>recorrências · manutenção]
  DB[(PostgreSQL 16)]
  RD[(Redis<br/>rate limiting)]
  M[SMTP / Mailpit]

  B --> P --> MW
  C --> P
  MW --> W & A
  W --> S
  A --> S
  S --> D
  S --> R --> DB
  S --> RD
  S --> M
  J --> S
```

## Camadas

| Pacote | Responsabilidade |
|---|---|
| `app/core` | Configuração validada, banco, logging JSON, segurança (Argon2id, tokens, CSRF), rate limiting, middlewares, handlers globais de erro, templates |
| `app/domain` | Regras puras e determinísticas: dinheiro, datas/competências, parcelamento e calendário de faturas, recorrências, efeitos no saldo |
| `app/models` | Modelos SQLAlchemy com constraints, índices e FKs compostas `(id, user_id)` |
| `app/repositories` | Acesso escopado por usuário (`get_owned`), paginação e ordenação por lista permitida |
| `app/services` | Casos de uso transacionais (contas, lançamentos, transferências, cartões, orçamentos, metas, recorrências, importação, relatórios, previsão, auditoria, idempotência) |
| `app/web` | Rotas das páginas, leitura de formulários e respostas HTMX (toast, fechamento de drawer, atualização de listas) |
| `app/api/v1` | Recursos REST com schemas Pydantic de entrada (estritos) e saída |
| `app/jobs` | Geração de recorrências, manutenção/retenção e agendador |
| `app/templates`, `app/static` | Layouts, componentes (macros), páginas, parciais HTMX; CSS Tailwind compilado; JS mínimo sem `eval` |

## Fluxo de uma operação financeira

```mermaid
sequenceDiagram
  participant U as Usuário
  participant W as Rota (web/api)
  participant S as Serviço
  participant I as IdempotencyGuard
  participant L as Ledger
  participant DB as PostgreSQL
  U->>W: POST + CSRF + Idempotency-Key
  W->>S: dados validados
  S->>I: begin(chave, payload)
  I->>DB: INSERT … ON CONFLICT DO NOTHING
  alt chave já usada
    I-->>S: id do recurso existente (replay)
  else nova
    S->>L: lock(contas)
    L->>DB: SELECT … FOR UPDATE (ordem por id)
    S->>DB: INSERT lançamento(s)
    S->>L: apply (checa saldo negativo)
    S->>DB: INSERT auditoria
    S->>DB: COMMIT (tudo ou nada)
  end
  S-->>W: recurso
  W-->>U: 204 + HX-Trigger (toast, refresh) ou JSON 201
```

## Regimes financeiros

| Regime | Usado em | Inclui | Exclui |
|---|---|---|---|
| Competência | receitas/despesas, categorias, orçamentos | lançamentos `income`/`expense` pela competência + parcelas de cartão pela fatura | transferências, pagamentos de fatura |
| Caixa | evolução do saldo, movimentações, faturas pagas | lançamentos efetivados pela data de efetivação, incluindo `card_payment` | compras no cartão |
| Projetado | previsão, fluxo projetado | previstos, recorrências futuras, faturas a pagar | transferências (no consolidado) |
| Gerencial | metas | reservas virtuais | — (não movimenta contas) |

Cada card e gráfico exibe o selo do regime utilizado.

## Modelo de dados

Dicionário completo em [DATA_MODEL.md](DATA_MODEL.md).

```mermaid
erDiagram
  USERS ||--o{ USER_SESSIONS : possui
  USERS ||--o{ EMAIL_TOKENS : possui
  USERS ||--o{ PASSWORD_RESET_TOKENS : possui
  USERS ||--o{ IDEMPOTENCY_KEYS : possui
  USERS ||--o{ ACCOUNTS : possui
  USERS ||--o{ CATEGORIES : possui
  USERS ||--o{ CREDIT_CARDS : possui
  USERS ||--o{ GOALS : possui
  ACCOUNTS ||--o{ TRANSACTIONS : movimenta
  CATEGORIES ||--o{ TRANSACTIONS : classifica
  TRANSFERS ||--|{ TRANSACTIONS : "2 pernas"
  ACCOUNTS ||--o{ TRANSFERS : "origem/destino"
  RECURRENCE_RULES ||--o{ TRANSACTIONS : gera
  IMPORT_BATCHES ||--o{ IMPORT_ROWS : contem
  IMPORT_BATCHES ||--o{ TRANSACTIONS : origina
  CATEGORIES ||--o{ BUDGETS : limita
  GOALS ||--o{ GOAL_ALLOCATIONS : reserva
  CREDIT_CARDS ||--o{ CARD_STATEMENTS : fatura
  CREDIT_CARDS ||--o{ CARD_PURCHASES : registra
  CARD_PURCHASES ||--|{ CARD_INSTALLMENTS : parcela
  CARD_STATEMENTS ||--o{ CARD_INSTALLMENTS : agrupa
  CARD_STATEMENTS ||--o{ CARD_PAYMENTS : recebe
  CARD_PAYMENTS ||--|| TRANSACTIONS : "saída de caixa"
  AUDIT_LOGS }o..o| USERS : "pseudonimizado"

  ACCOUNTS {
    uuid id PK
    uuid user_id FK
    bigint initial_balance_cents
    bigint current_balance_cents
    bool allow_negative
    timestamptz archived_at
  }
  TRANSACTIONS {
    uuid id PK
    uuid user_id FK
    uuid account_id FK
    varchar type "income|expense|transfer_in|transfer_out|card_payment"
    varchar status "planned|settled|cancelled"
    bigint amount_cents "CHECK > 0"
    date competence_date
    date settlement_date
    uuid transfer_id FK
    uuid recurrence_id FK
    varchar external_id "único por conta"
  }
  CARD_INSTALLMENTS {
    uuid id PK
    uuid purchase_id FK
    uuid statement_id FK
    smallint number
    varchar kind "charge|credit"
    bigint amount_cents
    date competence_date
  }
```

## Concorrência e consistência

* Locks pessimistas (`FOR UPDATE`) em contas, faturas, cartões e lotes de importação; ordem determinística por id.
* Unicidade no banco como última barreira: idempotência, ocorrências de recorrência, identificador bancário importado, orçamento ativo, faturas por mês.
* Testes em `tests/concurrency/` disparam operações simultâneas com conexões reais.

## Decisões registradas

Veja [docs/adr](adr/README.md).
