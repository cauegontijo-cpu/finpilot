# ADR-006 — Idempotência por tabela dedicada

**Status:** aceito · 2026-10

## Contexto
Duplo clique, reenvio e retentativas de rede não podem aplicar operações duas vezes.

## Decisão
Tabela `idempotency_keys` com único `(user_id, key)` gravada na MESMA transação da operação. Concorrentes com a mesma chave serializam no índice único; a segunda recebe o recurso já criado. Mesma chave com payload diferente → 409. Formulários web levam uma chave oculta; a API exige o cabeçalho `Idempotency-Key`.

## Consequências
Exatamente-uma-vez sem locks distribuídos; chaves expiram em 7 dias (job de manutenção).
