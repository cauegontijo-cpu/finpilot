# ADR-004 — Sessões no servidor + CSRF por HMAC

**Status:** aceito · 2026-10

## Contexto
Requisito de não guardar tokens no localStorage, invalidar sessões no logout e após troca de senha.

## Decisão
Cookie HttpOnly/SameSite=Lax (Secure e prefixo `__Host-` em produção) com token aleatório de 256 bits; o banco guarda só o SHA-256. Timeout ocioso (30 min) e absoluto (7 dias). Token CSRF = HMAC(segredo, sessão ou visitante anônimo), exigido em todo método mutável (form, HTMX ou API), mais verificação de Origin/Referer.

## Consequências
Revogação imediata e listagem de sessões ativas; uma consulta por requisição autenticada. Não há tokens Bearer para terceiros nesta versão (possível evolução).
