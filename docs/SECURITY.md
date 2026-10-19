# Segurança

Segurança é tratada como requisito funcional. Cada controle abaixo tem implementação e teste automatizado (`tests/security/`, `tests/integration/`, `tests/concurrency/`).

## Mapa OWASP Top 10 (2021)

| Risco | Controles | Onde |
|---|---|---|
| A01 Broken Access Control / IDOR / BOLA | Toda leitura por `get_owned(model, id, user_id)`; recurso de outro usuário = 404; FKs compostas `(id, user_id)` no banco; filtros de relatórios/dashboard validados; varredura automática garante que toda rota `/app` e `/api/v1` exige sessão | `app/repositories/base.py`, `tests/security/test_access_control.py` |
| A02 Falhas criptográficas | Argon2id (m=19 MiB, t=3) com rehash automático; tokens de 256 bits (`secrets`); somente SHA-256 de tokens no banco; HMAC-SHA256 para CSRF; HTTPS + HSTS em produção | `app/core/security.py` |
| A03 Injeção (SQL/HTML/CSV/PDF) | ORM parametrizado; `LIKE` com escape de curingas; Jinja2 com autoescape; JSON para gráficos com escape de `<>&`; CSV com neutralização de fórmulas (`= + - @ TAB CR`); texto escapado no ReportLab | `tests/security/test_access_control.py::test_sql_injection_*`, `test_html_is_escaped_in_listing`, `test_csv_formula_injection_neutralized` |
| A04 Design inseguro | Regras financeiras no backend, transações ACID, idempotência, locks ordenados, limites por configuração | `docs/BUSINESS_RULES.md` |
| A05 Configuração incorreta | `Settings` recusa produção com DEBUG, SECRET_KEY fraca, cookie não Secure, BASE_URL sem https, sem Redis ou com hosts curinga; erros sem stack trace; docs da API desligáveis | `app/core/config.py`, `tests/unit/test_config.py` |
| A06 Componentes vulneráveis | Dependências fixadas em `uv.lock`; assets de terceiros versionados localmente; binário do Tailwind verificado por SHA-256 no build | `Dockerfile` |
| A07 Falhas de autenticação | Rate limiting por IP e por conta; mensagem genérica de login; tempo equalizado para e-mail inexistente; política de senha (≥10, sem nome/e-mail, lista de comuns); rotação de sessão no login; timeout ocioso (30 min) e absoluto (7 dias) | `tests/security/test_auth_security.py` |
| A08 Integridade | Auditoria append-only (trigger + permissões); idempotência em operações críticas | migration inicial, `app/services/audit.py` |
| A09 Logging e monitoramento | Logs JSON com `request_id`, redação automática de chaves sensíveis, log de acesso sem query string; auditoria de login com falha (e-mail como HMAC) | `app/core/logging.py`, `tests/integration/test_logging.py` |
| A10 SSRF | A aplicação não faz requisições a URLs fornecidas pelo usuário | — |

## Sessões e CSRF

* Cookie `fp_session` (`__Host-fp_session` em HTTPS): `HttpOnly`, `SameSite=Lax`, `Secure` em produção, `Path=/`. Nenhum token em `localStorage`.
* O banco guarda apenas `sha256(token)`. Logout revoga a sessão; troca de senha revoga as demais; redefinição de senha revoga **todas**.
* CSRF: token = `HMAC(SECRET_KEY, "csrf:" + vínculo)`, em que o vínculo é a sessão (se houver) ou o cookie de visitante anônimo (`__Host-fp_anon` em HTTPS). Exigido em todo método não seguro — formulário (`csrf_token`), HTMX e API (`X-CSRF-Token`). Também verificamos `Origin`/`Referer`.
* Links de e-mail exibem uma página de confirmação e consomem o token apenas via POST (pré-carregadores de links não invalidam o token).

## Cabeçalhos

`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` — sem `unsafe-inline` (a página `/api/docs`, quando habilitada, tem CSP própria). Também: `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cache-Control: no-store` em páginas dinâmicas e `Strict-Transport-Security` somente em produção sob HTTPS.

## Rate limiting (HTTP 429 + `Retry-After`)

| Operação | Limite |
|---|---|
| Login por IP / por conta | 20 / 15 min · 5 / 15 min |
| Cadastro por IP | 5 / h |
| Recuperação de senha por IP / por conta | 5 / h · 3 / h |
| Verificação de e-mail e uso de tokens | 10 / h |
| Uploads de CSV | 20 / h |
| Operações financeiras de escrita | 120 / min por usuário |
| Reautenticação (troca de e-mail/senha, exclusão) | 10 / h |

Backend Redis em produção; memória nos testes.

## Uploads

Somente `.csv`; limite de bytes no middleware (antes do parsing) e no serviço; leitura de no máximo `limite + 1` bytes; recusa bytes nulos; UTF-8/Windows-1252; limite de linhas e colunas; nome do arquivo usado só para exibição (sanitizado); o arquivo nunca é gravado em disco; conteúdo tratado como texto.

## Dados e LGPD

Boas práticas adotadas — **sem alegação de conformidade jurídica**:

* Minimização: IP truncado (/24 IPv4, /48 IPv6), user-agent resumido, cartões apenas com 4 últimos dígitos, nenhum dado bancário real ou credencial bancária.
* Portabilidade: exportação JSON de todos os dados pessoais e financeiros (Configurações → Seus dados).
* Eliminação: exclusão definitiva com senha + digitação do e-mail; cascata em todas as tabelas; auditoria remanescente pseudonimizada.
* Logs não contêm senhas, tokens nem valores financeiros.

## Banco com privilégios mínimos

`finpilot_migrator` é dono do schema e aplica migrations; `finpilot_app` (aplicação) tem apenas DML e, em `audit_logs`, somente `INSERT`/`SELECT`. A retenção da auditoria é executada com o papel de manutenção.

## Riscos residuais conhecidos

* Sem 2FA/WebAuthn nesta versão.
* Rate limiting por IP depende de o proxy reverso ser o único a alcançar a aplicação (`--forwarded-allow-ips`).
* Envio de e-mail é síncrono (falhas de SMTP são registradas no log e não interrompem o fluxo).
* Proteção contra abuso distribuído (botnets) exigiria WAF/CDN.

## Como reportar vulnerabilidades

Abra uma issue privada (Security Advisory) no repositório; não publique detalhes antes da correção.
