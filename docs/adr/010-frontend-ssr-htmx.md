# ADR-010 — Frontend SSR com Jinja2 + HTMX e CSP estrita

**Status:** aceito · 2026-10

## Contexto
O prompt exige SSR com interatividade progressiva e mínimo de JavaScript.

## Decisão
Templates Jinja2 com autoescape, HTMX para drawers/filtros/listas, Tailwind v4 compilado pelo binário standalone (sem Node) e Chart.js/Lucide/Inter servidos localmente. CSP sem `unsafe-inline`: nenhum script ou estilo inline — dados de gráficos em `<script type=application/json>`, larguras e cores por classes geradas. Formulários funcionam sem JS (fallback para página completa).

## Consequências
Superfície XSS mínima; um único deploy. O binário do Tailwind falha com caminhos não ASCII ao usar `-o`, por isso a compilação escreve no stdout.
