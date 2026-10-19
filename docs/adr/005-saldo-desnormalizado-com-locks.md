# ADR-005 — Saldo desnormalizado alterado sob lock

**Status:** aceito · 2026-10

## Contexto
O saldo atual precisa ser lido rapidamente e nunca ficar inconsistente com operações concorrentes.

## Decisão
`accounts.current_balance_cents` é alterado exclusivamente pelo `Ledger` dentro da transação da operação, após `SELECT … FOR UPDATE` das contas envolvidas em ordem de id (evita deadlocks). A checagem de saldo negativo ocorre com a linha travada e considera o efeito líquido da operação.

## Consequências
Leitura O(1) do saldo; testes de concorrência com threads comprovam ausência de corrida; testes e o seed reconciliam o saldo com o histórico.
