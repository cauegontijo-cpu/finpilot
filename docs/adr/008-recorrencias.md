# ADR-008 — Recorrências por âncora e índice único

**Status:** aceito · 2026-10

## Contexto
Ocorrências não podem duplicar nem se perder com falhas do agendador.

## Decisão
A n-ésima ocorrência é calculada a partir da data inicial (âncora), com último dia válido do mês quando necessário (31/01 → 28/02 → 31/03). Ocorrências são lançamentos `planned` com `(recurrence_id, occurrence_date)` único; geração com `INSERT … ON CONFLICT DO NOTHING`, advisory lock no job e `FOR UPDATE SKIP LOCKED` por regra. `generated_until` permite recuperar o pendente. Ao criar, o retroativo é limitado a 60 dias.

## Consequências
Reexecutar o job é sempre seguro; editar a regra altera somente previstas futuras.
