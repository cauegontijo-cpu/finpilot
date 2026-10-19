# Dicionário de dados

Convenções: chaves primárias UUID v4 geradas pela aplicação; timestamps `timestamptz` em UTC (`created_at`/`updated_at` mantidos pelo banco); datas de negócio `date` (fuso de apresentação America/Sao_Paulo); dinheiro em centavos `BIGINT` com CHECK de faixa (|valor| ≤ R$ 1 bilhão); enums como `VARCHAR` + CHECK; todas as tabelas de usuário têm `user_id` com `ON DELETE CASCADE` (exceto `audit_logs`). O esquema é criado exclusivamente por migrations Alembic (`alembic/versions`).

## Identidade e segurança

| Tabela | Campos principais | Restrições / índices |
|---|---|---|
| `users` | `full_name`, `email`, `password_hash` (Argon2id), `email_verified_at`, `privacy_accepted_at`, `privacy_version`, `last_login_at` | único `lower(email)`; CHECK tamanho do nome |
| `user_sessions` | `token_hash` (SHA-256), `last_seen_at`, `expires_at`, `revoked_at`, `ip_address` (truncado), `user_agent` | único `token_hash` |
| `email_tokens` | `purpose` (verify/change_email), `token_hash`, `new_email`, `expires_at`, `used_at` | único `token_hash` |
| `password_reset_tokens` | `token_hash`, `expires_at`, `used_at` | único `token_hash` |
| `idempotency_keys` | `key`, `scope`, `request_hash`, `resource_id`, `expires_at` | único `(user_id, key)` |
| `audit_logs` | `user_id` (sem FK), `event_type`, `entity_type`, `entity_id`, `outcome`, `changes` (JSONB, sanitizado), `request_id`, `ip_address`, `user_agent`, `occurred_at` (`clock_timestamp()`) | índices `(user_id, occurred_at)`, `(entity_type, entity_id)`; trigger append-only; `finpilot_app` só INSERT/SELECT |

## Núcleo financeiro

| Tabela | Campos principais | Restrições / índices |
|---|---|---|
| `accounts` | `name`, `type`, `institution`, `color`, `icon`, `initial_balance_cents`, `current_balance_cents`, `start_date`, `allow_negative`, `archived_at` | único `(id, user_id)`; único parcial `(user_id, lower(name))` ativas |
| `categories` | `name`, `kind` (income/expense), `color`, `icon`, `archived_at` | único `(id, user_id)`; único parcial `(user_id, kind, lower(name))` ativas |
| `transfers` | `from_account_id`, `to_account_id`, `amount_cents`, `transfer_date`, `description`, `status` | CHECK origem ≠ destino; FKs compostas para contas |
| `transactions` | `account_id`, `type`, `status`, `origin`, `description`, `amount_cents`, `category_id`, `competence_date`, `settlement_date`, `due_date`, `notes`, `transfer_id`, `recurrence_id`, `occurrence_date`, `import_batch_id`, `external_id`, `import_fingerprint`, `cancelled_at` | CHECKs: valor > 0, categoria ⇔ receita/despesa, transfer_id ⇔ transferência, efetivada ⇒ data de efetivação, recorrência ⇔ ocorrência; únicos parciais `(recurrence_id, occurrence_date)` e `(account_id, external_id)`; índices por usuário+competência/efetivação/categoria/status; GIN trigram em `description` |

## Planejamento

| Tabela | Campos principais | Restrições / índices |
|---|---|---|
| `budgets` | `category_id`, `year`, `month`, `limit_cents`, `archived_at` | único parcial `(user_id, category_id, year, month)` ativos |
| `goals` | `name`, `description`, `target_cents`, `deadline`, `status`, `color`, `icon` | CHECK alvo > 0 |
| `goal_allocations` | `goal_id`, `amount_cents` (com sinal), `allocation_date`, `note`, `reverses_id` | CHECK ≠ 0; reversão negativa; `reverses_id` único |
| `recurrence_rules` | `account_id`, `category_id`, `type`, `description`, `amount_cents`, `frequency`, `interval`, `start_date`, `end_date`, `status`, `auto_settle`, `generated_until` | CHECKs de intervalo e datas; FKs compostas |

## Importação

| Tabela | Campos principais | Restrições / índices |
|---|---|---|
| `import_batches` | `account_id`, `status`, `display_name` (somente exibição), `file_sha256`, `encoding`, `delimiter`, `has_header`, `headers`, `mapping`, `report`, `expires_at` | FK composta para conta |
| `import_rows` | `row_number`, `raw` (JSONB), `status`, `occurred_on`, `description`, `amount_cents`, `category_name`, `external_id`, `fingerprint`, `errors`, `selected`, `transaction_id` | único `(batch_id, row_number)` |

## Cartões

| Tabela | Campos principais | Restrições / índices |
|---|---|---|
| `credit_cards` | `name`, `institution`, `limit_cents`, `closing_day`, `due_day`, `last4`, `color`, `archived_at` | CHECK dias 1–31; `last4` apenas 4 dígitos (nunca número completo/CVV) |
| `card_statements` | `card_id`, `year`, `month`, `closing_date`, `due_date` | único `(card_id, year, month)`; CHECK vencimento ≥ fechamento |
| `card_purchases` | `card_id`, `category_id`, `description`, `purchase_date`, `total_cents`, `installments_count`, `status`, `refunded_at` | CHECK 1–48 parcelas e total ≥ parcelas |
| `card_installments` | `purchase_id`, `statement_id`, `number`, `kind` (charge/credit), `amount_cents`, `competence_date`, `status` | único `(purchase_id, number, kind)` |
| `card_payments` | `statement_id`, `account_id`, `transaction_id`, `amount_cents`, `payment_date`, `status` | `transaction_id` único |

## Exclusão e arquivamento

| Dado | Estratégia |
|---|---|
| Contas, categorias, cartões, metas | arquivamento; exclusão física só sem histórico |
| Lançamentos, transferências, compras, pagamentos | nunca apagados — cancelados/estornados com auditoria |
| Ocorrências previstas futuras de recorrência pausada/cancelada/editada | removidas (nunca tiveram efeito financeiro), com auditoria |
| Sessões, tokens, chaves de idempotência, lotes não confirmados | expiram e são removidos pelo job de manutenção |
| Usuário | exclusão em cascata de todos os dados; auditoria fica pseudonimizada até o fim da retenção |
