# ADR-002 — SQLAlchemy 2 síncrono + psycopg 3

**Status:** aceito · 2026-10

## Contexto
Operações financeiras dependem de locks de linha e transações curtas e previsíveis.

## Decisão
Sessões síncronas (uma por requisição) e endpoints `def` executados no threadpool do Starlette. Serviços chamam `commit()` explicitamente ao final de cada operação; exceções resultam em rollback.

## Consequências
Código mais simples de raciocinar e testar (inclusive concorrência com threads). Throughput adequado ao perfil de uso; migração para async seria possível sem mudar o domínio.
