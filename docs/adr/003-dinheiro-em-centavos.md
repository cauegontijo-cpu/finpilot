# ADR-003 — Dinheiro em centavos (BIGINT)

**Status:** aceito · 2026-10

## Contexto
Float gera erros de arredondamento inaceitáveis em finanças.

## Decisão
Valores armazenados como `BIGINT` em centavos, com CHECK de faixa (até R$ 1 bilhão). Conversões texto↔centavos usam `Decimal` com `ROUND_HALF_EVEN` em `app/domain/money.py`. A API expõe `*_cents` (inteiro) e `amount` (string decimal exata).

## Consequências
Somas exatas e determinísticas. O CI falha se `float(` aparecer nos módulos monetários.
