# ADR-007 — Cartões: competência nas parcelas, caixa no pagamento

**Status:** aceito · 2026-10

## Contexto
Evitar dupla contagem entre compra no cartão e pagamento da fatura.

## Decisão
Cada parcela é despesa na competência (data de fechamento) da sua fatura. Fechamento: compra até o dia de fechamento (inclusive) entra na fatura do mês; depois, na seguinte; dias inexistentes viram o último dia do mês. Vencimento no mesmo mês se `due_day > closing_day`, senão no seguinte. Centavos residuais nas primeiras parcelas. O pagamento é um lançamento `card_payment` (saída de caixa, sem categoria) e nunca entra no total de despesas. Limite utilizado = parcelas ativas − créditos − pagamentos (calculado, não armazenado). Estorno: cancela parcelas de faturas abertas e lança crédito na fatura aberta para parcelas já faturadas. Pagamento acima do devido é recusado (crédito positivo fora do escopo).

## Consequências
Os três regimes (competência, caixa, limite) ficam separados e auditáveis. Introduz o tipo `card_payment` além de receita/despesa/transferência.
