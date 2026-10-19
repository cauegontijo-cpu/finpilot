# ADR-001 — Monólito modular em camadas

**Status:** aceito · 2026-10

## Contexto
Precisamos de uma aplicação completa (web + API + jobs) mantida por uma pessoa, com regras financeiras consistentes entre interfaces.

## Decisão
Monólito FastAPI com camadas `web/` e `api/v1/` (apresentação) → `services/` (regras e transações) → `repositories/` + `models/` (persistência). Regras puras e determinísticas ficam em `domain/` (sem I/O). Web e API chamam exatamente os mesmos serviços.

## Consequências
Deploy único e simples; testes de regras sem HTTP; sem duplicação de lógica. Escalar para serviços separados exigiria extrair módulos — os limites já estão traçados por pacote.
