# Regras de negócio

Cada regra indica **onde é aplicada** e **qual teste a comprova**. Toda validação acontece no backend (RN019); a interface apenas ajuda o usuário.

Caminhos de teste relativos a `tests/`. Abreviações: `ledger` = `integration/test_ledger_rules.py`, `cards` = `integration/test_cards.py`, `ab` = `integration/test_analytics_budgets.py`, `gr` = `integration/test_goals_recurrences.py`, `ir` = `integration/test_imports_reports.py`, `conc` = `concurrency/`.

## Contas (RN001–RN010)

| Regra | Implementação | Testes |
|---|---|---|
| RN001 Conta pertence a exatamente um usuário | `accounts.user_id NOT NULL` + FK; FKs compostas `(id, user_id)` nas tabelas filhas | `security/test_access_control.py` |
| RN002 Sem acesso a contas de terceiros | `repositories/base.get_owned` (404 para id alheio) | `test_cannot_view_or_edit_other_users_account`, `ledger::test_user_cannot_use_other_users_account_or_category` |
| RN003 Saldo inicial não é receita | `AccountService.create` grava em `initial_balance_cents`; agregações só somam lançamentos | `ledger::test_initial_balance_is_not_income` |
| RN004 Só efetivados alteram saldo | `domain/ledger.balance_effect` | `ledger::test_planned_does_not_change_balance` |
| RN005/RN006 Receita aumenta, despesa diminui | `domain/ledger.signed_effect` + `Ledger.apply` | `ledger::test_settled_income_and_expense_change_balance` |
| RN007 Transferência altera duas contas sem receita/despesa | `TransferService` (pernas `transfer_out`/`transfer_in`) | `ledger::test_transfer_moves_money_without_income_or_expense` |
| RN008 Sem saldo negativo quando não permitido | `Ledger.apply_delta` (checagem com a linha travada, efeito líquido) | `ledger::test_negative_balance_refused_when_not_allowed`, `test_edit_increasing_expense_beyond_balance_refused` |
| RN009 Conta com histórico não é excluída | `AccountService.delete` + arquivamento (`archived_at`) | `ledger::test_account_with_history_cannot_be_deleted_only_archived` |
| RN010 Concorrência não gera saldo inconsistente | `SELECT … FOR UPDATE` ordenado por id | `conc/test_ledger_concurrency.py` (despesas simultâneas, transferências opostas) |

Precisão: centavos `BIGINT`, `Decimal` com `ROUND_HALF_EVEN` (`domain/money.py`) — `unit/test_money.py` (inclui teste de propriedade com Hypothesis).

**Formatos monetários aceitos (RN053):** `1234,56` · `1.234,56` · `R$ 1.234,56` · `-1.234,56` · `(1.234,56)` · `1.234,56-` · `1.234,56 D`/`C` · `1234.56` · `1,234.56`. Ambíguos como `12,345` são recusados.

## Transações (RN011–RN020)

| Regra | Implementação | Testes |
|---|---|---|
| RN011 Valor > 0 | CHECK `amount_cents > 0` + `TransactionService._validate` | `ledger::test_amount_must_be_positive` |
| RN012 Categoria do mesmo usuário e tipo | FK composta `(category_id, user_id)` + `CategoryService.require_for_type` | `ledger::test_category_kind_must_match_type` |
| RN013 Previstos não alteram saldo | `balance_effect` | `ledger::test_planned_does_not_change_balance` |
| RN014 Exatamente um efeito por efetivação | efeito aplicado só na transição para `settled`; `settle` idempotente | `ledger::test_settle_planned` |
| RN015 Cancelamento por estorno auditável | `TransactionService.cancel` (status `cancelled` + evento `transaction.reversed`) | `ledger::test_cancel_settled_reverses_and_keeps_history` |
| RN016 Correções preservam saldo e histórico | `update` = estorno do efeito antigo + novo efeito na mesma transação; diff na auditoria | `ledger::test_edit_settled_moves_amount_between_accounts_consistently` |
| RN017 Sem aplicação dupla | `IdempotencyGuard` | `ledger::test_repeated_request_with_same_key_applies_once`, `conc::test_duplicate_request_same_idempotency_key_applies_once` |
| RN018 Atomicidade | lançamento + saldo + idempotência + auditoria num único commit | `ledger::test_transfer_insufficient_funds_rolls_back_both_legs` |
| RN019 Validação no backend | serviços + Pydantic (`extra="forbid"`) | `integration/test_api.py`, `security/test_access_control.py::test_mass_assignment_fields_are_ignored` |
| RN020 Somente as próprias transações | consultas filtradas por `user_id` | `test_cannot_touch_other_users_transactions_and_transfers` |

Efetivação futura é recusada (registre como prevista). Datas: competência (reconhecimento), efetivação (caixa) e vencimento (previsão).

## Transferências (RN021–RN028)

| Regra | Implementação | Testes |
|---|---|---|
| RN021 Origem ≠ destino | CHECK no banco + validação | `ledger::test_transfer_same_account_refused` |
| RN022 Ambas do usuário | `AccountService.require_active` + FK composta | `ledger::test_transfer_to_other_users_account_refused` |
| RN023 Valor positivo | CHECK + validação | idem RN011 |
| RN024/RN025 Atômica; falha reverte tudo | duas pernas no mesmo commit | `ledger::test_transfer_insufficient_funds_rolls_back_both_legs` |
| RN026 Não conta como receita/despesa | agregações filtram `income`/`expense` | `ab::test_monthly_competence_excludes_transfers_and_separates_planned` |
| RN027 Cancelamento reverte os dois lados | `TransferService.cancel` | `ledger::test_transfer_cancel_reverts_both_sides` |
| RN028 Idempotência | `IdempotencyGuard` | `ledger::test_transfer_idempotency` |

## Categorias

Categorias por usuário (14 padrão criadas no cadastro), nome único por usuário + tipo entre as ativas (índice parcial), arquivamento sem perder histórico, exclusão só sem uso — `integration/test_categories.py`.

## Orçamentos (RN029–RN037)

| Regra | Implementação | Testes |
|---|---|---|
| RN029 Mês, ano, categoria e limite | CHECKs + `BudgetService.create` | `ab::test_budget_requires_expense_category` |
| RN030 Um ativo por categoria/competência | índice único parcial | `ab::test_duplicate_budget_same_category_month_rejected` |
| RN031 Consumo = despesas efetivadas + parcelas de cartão da competência | `BudgetService.consumption` | `cards::test_installments_count_in_budget_competence` |
| RN032 Transferências não consomem | filtro `type = expense` | `ab::test_budget_ignores_transfers_and_recalculates_after_cancel` |
| RN033 Previstos separados | coluna `planned` | `ab::test_budget_usage_levels_and_planned_separation` |
| RN034/RN035 Avisos 80% e 100% | `BudgetStatus.level` | idem |
| RN036 Recalcula após estornos | consumo calculado na consulta | `ab::test_budget_ignores_transfers_and_recalculates_after_cancel` |
| RN037 Períodos no fuso de apresentação | `domain/dates` (datas civis em America/Sao_Paulo) | `unit/test_recurrence_dates.py::test_local_date_uses_sao_paulo_timezone` |

## Metas (RN038–RN043)

| Regra | Implementação | Testes |
|---|---|---|
| RN038 Alvo positivo | CHECK + validação | `gr::test_goal_target_must_be_positive` |
| RN039/RN040 Alocação é reserva virtual | `goal_allocations` sem lançamentos | `gr::test_allocations_are_virtual_and_do_not_touch_balance` |
| RN041 Disponível × reservado | `GoalService.reservation_summary` | idem |
| RN042 Alerta quando reservado > disponível | `ReservationSummary.over_reserved` | `gr::test_over_reservation_is_flagged_without_artificial_entries` |
| RN043 Progresso recalculado | agregação + status automático | `gr::test_progress_status_and_reversal` |

Estimativa de conclusão: média das reservas líquidas dos últimos 3 meses (projeção linear simples).

## Recorrências (RN044–RN050)

| Regra | Implementação | Testes |
|---|---|---|
| RN044 Vínculo com a regra | `transactions.recurrence_id` + `occurrence_date` | `gr::test_generation_links_occurrences_and_uses_last_valid_day` |
| RN045 Sem duplicatas | índice único `(recurrence_id, occurrence_date)` | `gr::test_generation_is_idempotent` |
| RN046 Dias 29–31 → último dia válido | `domain/recurrence.nth_occurrence` (âncora) | `unit/test_recurrence_dates.py` |
| RN047 Edição não altera efetivadas | `RecurrenceService.update` (somente previstas futuras) | `gr::test_edit_changes_only_future_planned_occurrences` |
| RN048 Geração idempotente | `ON CONFLICT DO NOTHING` | `gr::test_generation_is_idempotent` |
| RN049 Ocorrências nascem previstas | padrão; `auto_settle` explícito e com checagem de saldo | `gr::test_auto_settle_*` |
| RN050 Falhas do agendador não perdem/duplicam | `generated_until`, advisory lock, `SKIP LOCKED` | `conc::test_concurrent_recurrence_jobs_never_duplicate` |

Job: `python -m app.jobs.recurrences` (serviço `scheduler` a cada 15 min).

## Importação CSV (RN051–RN060)

| Regra | Implementação | Testes |
|---|---|---|
| RN051 Nada efetivado antes da confirmação | lotes em `import_batches/rows` | `ir::test_upload_parses_without_creating_transactions` |
| RN052 Confirmação do formato de data | seleção do formato + aviso de ambiguidade | `ImportService.date_ambiguity` |
| RN053 Formatos monetários brasileiros | `domain/money.parse_brl` | `unit/test_money.py` |
| RN054 Duplicatas por id bancário ou fingerprint | `ImportService.apply_mapping` | `ir::test_fingerprint_duplicates_without_bank_id_need_review` |
| RN055 Reimportação sem duplicar | id bancário único por conta; nada pré-selecionado | `ir::test_reimport_same_file_flags_duplicates` |
| RN056 Motivos específicos | `ImportRow.errors` | `ir::test_mapping_validates_rows_with_specific_reasons` |
| RN057 Conteúdo nunca executado | parsing como texto; exportações neutralizam fórmulas | `ir::test_confirm_creates_selected_rows_atomically_and_reports` |
| RN058 Limites de tamanho/linhas | `IMPORT_MAX_BYTES`, `IMPORT_MAX_ROWS` + middleware de corpo | `ir::test_upload_size_and_row_limits` |
| RN059 Isolamento | lotes por `user_id` | `ir::test_other_user_cannot_access_import` |
| RN060 Transacional com relatório | savepoint por linha + relatório | `ir::test_confirm_*`, `conc::test_duplicate_import_confirmation_applies_once` |

**Fingerprint:** `SHA-256(conta | data | valor com sinal | descrição normalizada | n-ésima repetição no arquivo)`.

## Cartões (RN061–RN070)

| Regra | Implementação | Testes |
|---|---|---|
| RN061 Soma das parcelas = total | `domain/installments.split_installments` | `unit/test_installments.py` (propriedade), `cards::test_installments_sum_exactly_and_follow_statements` |
| RN062 Centavos residuais determinísticos | resíduo nas primeiras parcelas | idem |
| RN063 Parcela consistente | CHECK `total >= parcelas`; 1 a 48 | `cards::test_inconsistent_installments_rejected` |
| RN064 Compra compromete o limite | `card_debt` + recusa acima do disponível (lock do cartão) | `cards::test_limit_is_committed_and_purchase_over_limit_refused`, `conc/test_card_concurrency.py` |
| RN065 Pagamento reduz a conta | lançamento `card_payment` via `Ledger` | `cards::test_payment_reduces_cash_not_expense_and_frees_limit` |
| RN066 Pagamento não é despesa | `card_payment` excluído de receitas/despesas | idem |
| RN067 Atribuição às faturas | `domain/installments` (regra no ADR-007) | `unit/test_installments.py`, `cards::test_purchase_after_closing_and_year_rollover`, `test_february_closing_day_beyond_month_end` |
| RN068 Limite reflete compras, pagamentos e estornos | limite calculado, nunca armazenado | `cards::test_refund_after_closing_creates_credit_and_preserves_history` |
| RN069 Sem pagamento acima do devido | `CardService.pay` (fatura travada) | `cards::test_overpayment_refused`, `conc::test_duplicate_statement_payment_charges_once` |
| RN070 Cancelamentos preservam histórico | parcelas canceladas + crédito de estorno | `cards::test_refund_*`, `test_cancel_payment_restores_account_and_debt` |

## Previsão de caixa (RN071–RN077)

| Regra | Implementação | Testes |
|---|---|---|
| RN071 Parte do saldo realizado | `ForecastService.build` | `ab::test_forecast_starts_from_realized_balance_and_finds_negative_day` |
| RN072 Previsto ≠ realizado | eventos futuros separados do saldo inicial | idem |
| RN073 Data esperada do desembolso | vencimento, ou competência na falta dele | idem |
| RN074 Transferências não alteram o consolidado | ignoradas no consolidado; consideradas por conta | idem |
| RN075 Faturas como saída, sem somar compras | saldo devido na data de vencimento | `cards::test_forecast_includes_statement_due_as_cash_outflow` |
| RN076 Ausência de dados = incerteza | avisos em `Forecast.warnings` | `ab::test_forecast_without_events_reports_uncertainty` |
| RN077 Determinística, sem IA | somente lançamentos conhecidos | documentado na tela e aqui |
