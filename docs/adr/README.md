# Registros de decisão de arquitetura (ADR)

| ADR | Decisão |
|-----|---------|
| [ADR-001](001-monolito-modular.md) | Monólito modular em camadas |
| [ADR-002](002-sqlalchemy-sincrono.md) | SQLAlchemy 2 síncrono + psycopg 3 |
| [ADR-003](003-dinheiro-em-centavos.md) | Dinheiro em centavos (BIGINT) |
| [ADR-004](004-sessoes-server-side.md) | Sessões no servidor + CSRF por HMAC |
| [ADR-005](005-saldo-desnormalizado-com-locks.md) | Saldo desnormalizado alterado sob lock |
| [ADR-006](006-idempotencia.md) | Idempotência por tabela dedicada |
| [ADR-007](007-cartoes-e-faturas.md) | Cartões: competência nas parcelas, caixa no pagamento |
| [ADR-008](008-recorrencias.md) | Recorrências por âncora e índice único |
| [ADR-009](009-auditoria-append-only.md) | Auditoria append-only |
| [ADR-010](010-frontend-ssr-htmx.md) | Frontend SSR com Jinja2 + HTMX e CSP estrita |
