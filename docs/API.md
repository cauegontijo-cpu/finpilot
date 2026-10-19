# API REST v1

Base: `/api/v1`. Documentação interativa OpenAPI em `/api/docs` (habilitada por padrão em desenvolvimento; `API_DOCS_ENABLED=false` em produção). Esquema: `/api/openapi.json`.

## Convenções

| Tema | Regra |
|---|---|
| Autenticação | Sessão por cookie HttpOnly obtida em `POST /auth/login` |
| CSRF | Todo método mutável exige `X-CSRF-Token` (obtenha em `GET /auth/csrf`; muda após o login) |
| Idempotência | `Idempotency-Key` (8–80 caracteres) **obrigatório** em `POST /transactions`, `/transfers`, `/cards/{id}/purchases`, `/statements/{id}/payments`, `/imports/{id}/confirm` |
| Dinheiro | Inteiros em centavos (`amount_cents`); respostas também trazem `amount` como string decimal exata |
| Datas | `YYYY-MM-DD` (datas civis); timestamps em ISO 8601 UTC |
| Paginação | `page` (≥1) e `page_size` (≤100); resposta `{items, total, page, page_size}` |
| Ordenação | `sort=campo` ou `sort=-campo`, somente campos permitidos (outros → 422) |
| Entrada | Campos desconhecidos são rejeitados (422) — sem mass assignment |
| Erros | `application/problem+json` (RFC 9457) |

```json
{
  "type": "https://finpilot.local/problems/RN008",
  "title": "Operação não permitida",
  "status": 422,
  "detail": "Saldo insuficiente na conta “Corrente”, que não permite saldo negativo.",
  "code": "RN008",
  "request_id": "4f1c2a9b8e7d6c5a"
}
```

| Status | Uso |
|---|---|
| 200/201/202/204 | sucesso (201 com `Location` nas criações) |
| 401 | sem sessão / credenciais inválidas |
| 403 | CSRF inválido ou e-mail não verificado |
| 404 | inexistente **ou de outro usuário** |
| 409 | conflito (duplicidade, chave de idempotência com outro payload) |
| 413 | corpo acima do limite |
| 422 | validação ou regra de negócio (campo `code` com a RN) |
| 429 | rate limit (`Retry-After`) |

## Recursos

| Recurso | Endpoints |
|---|---|
| Auth | `GET /auth/csrf` · `POST /auth/register` · `POST /auth/verify-email` · `POST /auth/login` · `POST /auth/logout` · `POST /auth/password-reset` · `POST /auth/password-reset/confirm` |
| Usuário | `GET/PATCH/DELETE /users/me` · `POST /users/me/password` · `GET/DELETE /users/me/sessions` · `GET /users/me/export` |
| Contas | `GET/POST /accounts` · `GET/PUT/DELETE /accounts/{id}` · `POST /accounts/{id}/archive` · `POST /accounts/{id}/restore` |
| Categorias | `GET/POST /categories` · `PUT/DELETE /categories/{id}` · `POST /categories/{id}/archive` |
| Transações | `GET/POST /transactions` · `GET /transactions/totals` · `GET/PUT /transactions/{id}` · `POST /transactions/{id}/settle` · `POST /transactions/{id}/cancel` · `GET /transactions/{id}/history` |
| Transferências | `GET/POST /transfers` · `GET /transfers/{id}` · `POST /transfers/{id}/settle` · `POST /transfers/{id}/cancel` |
| Orçamentos | `GET /budgets?year&month` · `POST /budgets` · `PATCH/DELETE /budgets/{id}` |
| Metas | `GET/POST /goals` · `GET/PUT /goals/{id}` · `GET/POST /goals/{id}/allocations` · `POST /goals/{id}/allocations/{aid}/reverse` · `POST /goals/{id}/archive` |
| Recorrências | `GET/POST /recurrences` · `GET/PUT /recurrences/{id}` · `GET /recurrences/{id}/next` · `POST /recurrences/{id}/{pause\|resume\|cancel}` |
| Cartões | `GET/POST /cards` · `GET/PUT /cards/{id}` · `GET/POST /cards/{id}/purchases` · `POST /purchases/{id}/refund` |
| Faturas | `GET /cards/{id}/statements` · `GET /statements/{id}` · `POST /statements/{id}/payments` · `POST /payments/{id}/cancel` |
| Importações | `POST /imports` (multipart) · `GET/DELETE /imports/{id}` · `GET /imports/{id}/rows` · `POST /imports/{id}/mapping` · `POST /imports/{id}/confirm` |
| Relatórios | `GET /reports` · `GET /reports/{key}?start=AAAA-MM&end=AAAA-MM[&format=csv\|pdf]` |
| Dashboard | `GET /dashboard?year&month[&account_id&category_id]` · `GET /dashboard/forecast?days=30\|60\|90` |

## Exemplo com curl

```bash
B=http://localhost:8000/api/v1
CSRF=$(curl -s -c jar -b jar $B/auth/csrf | jq -r .csrf_token)
curl -s -c jar -b jar -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"email":"demo@example.com","password":"<sua senha>"}' $B/auth/login
CSRF=$(curl -s -c jar -b jar $B/auth/csrf | jq -r .csrf_token)   # novo vínculo após login

curl -s -b jar $B/accounts | jq '.[0].id'
curl -s -b jar -H "X-CSRF-Token: $CSRF" -H "Idempotency-Key: $(uuidgen)" -H 'Content-Type: application/json' \
  -d '{"type":"expense","account_id":"<id>","category_id":"<id>","description":"Mercado",
       "amount_cents":12345,"competence_date":"2026-10-10","settlement_date":"2026-10-10"}' \
  $B/transactions
```

Repetir a última chamada com a mesma `Idempotency-Key` devolve o mesmo lançamento, sem debitar de novo.
