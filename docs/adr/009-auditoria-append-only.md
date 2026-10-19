# ADR-009 — Auditoria append-only

**Status:** aceito · 2026-10

## Contexto
Registros de auditoria não podem ser alterados pela interface comum.

## Decisão
Tabela `audit_logs` gravada na transação da operação (falhas de login em transação separada). Trigger rejeita UPDATE/DELETE; o papel `finpilot_app` só tem INSERT/SELECT. Retenção padrão de 5 anos (falhas de login: 180 dias) via job executado com papel de manutenção. `user_id` sem FK: após exclusão da conta, os registros ficam pseudonimizados (sem nome/e-mail).

## Consequências
Rastreabilidade de cancelamentos e alterações; o histórico de cada lançamento é lido dessa tabela.
