# FinPilot — material de divulgação

## 1. Apresentação para recrutadores

O **FinPilot** é uma plataforma de gestão financeira pessoal que construí de ponta a ponta em **Python**. Ela cobre backend, API REST, banco de dados, interface, segurança, testes e implantação com Docker. Não é um protótipo visual: cada botão chama serviços reais, que persistem no PostgreSQL com transações ACID.

O que o projeto demonstra:

* Modelagem de **regras financeiras reais**, como competência × caixa, parcelamento exato ao centavo, faturas por fechamento e vencimento, estornos auditáveis e previsão de caixa.
* **Segurança aplicada** de acordo com o OWASP Top 10, com testes automatizados para cada controle.
* Engenharia para **concorrência**: duas operações simultâneas nunca deixam o saldo inconsistente.
* Qualidade de código: Ruff e **mypy em modo estrito sem pendências**, CI no GitHub Actions e documentação completa.

## 2. Principais desafios técnicos

1. **Consistência sob concorrência.** O saldo é alterado por um único componente (`Ledger`), com `SELECT … FOR UPDATE` em ordem determinística. Testes com threads e conexões reais simulam despesas simultâneas, transferências opostas, pagamentos duplicados de fatura e compras paralelas contra o limite do cartão.
2. **Idempotência de verdade.** Duplo clique, reenvio e retentativa de rede não aplicam a operação duas vezes. A chave fica registrada na mesma transação, e requisições concorrentes com a mesma chave são serializadas pelo índice único.
3. **Cartões sem dupla contagem.** A compra vira despesa nas parcelas, na competência da fatura. O pagamento da fatura é só uma saída de caixa. O limite disponível é sempre calculado, nunca guardado num contador.
4. **Datas e fuso.** Recorrências do dia 31 caem no último dia válido de cada mês, e o cálculo parte sempre da data inicial, para não "derivar". Competências são calculadas no fuso America/Sao_Paulo.
5. **Frontend moderno com CSP estrita.** É SSR com HTMX, sem nenhum script ou estilo inline: os dados dos gráficos vão em blocos JSON, e larguras e cores são classes geradas.

## 3. Tecnologias

Python 3.12 · FastAPI · Pydantic 2 · SQLAlchemy 2 · Alembic · PostgreSQL 16 · Redis · Jinja2 · HTMX · Tailwind CSS v4 · Chart.js · Argon2id · ReportLab · Pytest · Hypothesis · Playwright · Ruff · mypy · Docker · Caddy · GitHub Actions.

## 4. Destaques de regras de negócio

* Saldo inicial nunca é receita. Somente lançamentos efetivados alteram o saldo.
* Uma conta que não permite saldo negativo recusa a operação, inclusive em edições e estornos.
* Transferências são atômicas e não entram em receitas nem despesas.
* Orçamentos mostram o previsto separado do realizado, com avisos em 80% e 100%, e incluem as parcelas de cartão do mês.
* Metas usam reservas virtuais: não debitam o banco e o sistema alerta quando o total reservado supera o disponível.
* A importação de CSV detecta duplicatas pelo ID do banco ou por uma impressão digital (fingerprint) e nunca grava nada antes da confirmação.
* A previsão de 30, 60 e 90 dias parte do saldo realizado e aponta a data em que o saldo ficaria negativo.

As 77 regras estão rastreadas até o código e o teste correspondentes em `docs/BUSINESS_RULES.md`.

## 5. Destaques de segurança

* Senhas com Argon2id. Sessões server-side em cookies HttpOnly e Secure com prefixo `__Host-`. Tokens guardados só como hash.
* CSRF em todo formulário, requisição HTMX e chamada da API, com verificação de Origin.
* IDOR/BOLA bloqueado em todas as consultas: um recurso de outro usuário responde 404. As FKs compostas `(id, user_id)` reforçam isso no próprio banco.
* Rate limiting com resposta 429 no login, no cadastro, na recuperação de senha, nos uploads e nas operações financeiras.
* CSP sem `unsafe-inline`, HSTS em produção e CSV protegido contra injeção de fórmulas.
* Auditoria append-only (trigger + papel do banco sem UPDATE/DELETE) e logs JSON com redação de segredos.
* A aplicação se recusa a iniciar em produção com configuração insegura.

## 6. Roteiro de demonstração em vídeo (≈ 4 minutos)

| Tempo | Cena |
|---|---|
| 0:00 | Landing page → cadastro → e-mail de verificação no Mailpit → login |
| 0:30 | Dashboard com dados de demonstração: KPIs, selos de regime (competência, caixa, projetado), troca de mês e conta via HTMX, modo escuro |
| 1:10 | Nova despesa no drawer: validação em pt-BR, toast e lista atualizada sem recarregar a página. Estorno e histórico do lançamento |
| 1:40 | Tentativa de despesa acima do saldo numa conta sem saldo negativo: operação recusada |
| 2:00 | Cartão: compra de R$ 100,00 em 3x, com o cronograma 33,34 + 33,33 + 33,33 na tela. Fatura, pagamento parcial e limite disponível |
| 2:40 | Importação de CSV: mapeamento, prévia com linha inválida e possível duplicata, confirmação e relatório |
| 3:10 | Previsão de 90 dias com a data de saldo negativo, e relatório exportado em PDF |
| 3:30 | Terminal: `make check` (Ruff, mypy e testes) e um trecho do teste de concorrência |
| 3:50 | Encerramento: arquitetura e segurança no README |

## 7. Sugestão de publicação

> Projeto novo no portfólio: o **FinPilot**, uma plataforma de finanças pessoais que construí do zero em Python. Está no ar e qualquer pessoa pode testar.
>
> A ideia surgiu de um incômodo: planilhas e apps simples contam a compra no cartão duas vezes (na compra e no pagamento da fatura), tratam transferência como receita e deixam o saldo divergir do extrato. No FinPilot, cada número diz de onde vem: o que já aconteceu, o que é do mês e o que ainda vai vencer.
>
> **O que dá para fazer:** contas, cartões com parcelamento e faturas, orçamentos com alertas, metas, lançamentos recorrentes, importação de extrato em CSV, relatórios em PDF e previsão do saldo para os próximos 90 dias, com modo claro e escuro.
>
> **O que mais me ensinou:**
> • Dinheiro guardado em centavos e parcelas exatas: R$ 100 em 3x vira 33,34 + 33,33 + 33,33.
> • Duas operações ao mesmo tempo nunca deixam o saldo errado (testei com operações simultâneas de verdade).
> • Segurança desde o início: senhas com Argon2id, proteção contra CSRF, limite de tentativas, auditoria que não pode ser apagada e cada usuário enxergando só os próprios dados.
> • 261 testes automatizados, verificação de tipos e integração contínua no GitHub.
>
> **Stack:** Python, FastAPI, PostgreSQL, SQLAlchemy, HTMX, Tailwind, Redis e Docker.
>
> 🔗 Teste online: [link da demo]
> 💻 Código: https://github.com/cauegontijo-cpu/finpilot
>
> É uma demo: crie a conta com seu e-mail, mas não use dados financeiros reais. O primeiro acesso pode levar cerca de 1 minuto, porque o servidor gratuito hiberna quando fica sem uso.
>
> Feedbacks são muito bem-vindos!
>
> #Python #FastAPI #PostgreSQL #DesenvolvimentoWeb #FullStack #Portfolio

*Os números (261 testes, sendo 259 automatizados e 2 ponta a ponta com Playwright; cobertura de 90,6% em services e 96,1% em domain) vêm da execução local registrada em `docs/TESTING.md`. Atualize-os se o projeto mudar.*
