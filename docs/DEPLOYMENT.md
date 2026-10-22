# Implantação, operação e backup

## Arquitetura de produção

`docker-compose.prod.yml`: **Caddy** (único serviço com portas públicas 80/443, HTTPS automático) → **web** (Uvicorn com 4 workers, sem `--reload`, filesystem somente leitura, usuário sem privilégios) · **scheduler** (recorrências e manutenção) · **migrate** (executa `alembic upgrade head` e termina) · **PostgreSQL 16** e **Redis 7** apenas na rede interna (`internal: true`), sem portas publicadas.

## Demo pública na Render

Forma mais simples de ter um link público para qualquer pessoa testar, inclusive o cadastro com e-mail real. O [render.yaml](../render.yaml) (Blueprint) cria três recursos gratuitos: o serviço **web** (imagem do `Dockerfile`), o **PostgreSQL** e o **Key Value** (Redis). PostgreSQL e Redis ficam sem acesso externo.

### 1. E-mail (Brevo)

1. Crie uma conta gratuita em <https://www.brevo.com> (300 e-mails por dia).
2. Em **Senders, Domains & Dedicated IPs → Senders**, adicione e verifique o e-mail remetente (pode ser um Gmail seu).
3. Em **SMTP & API → API Keys**, gere uma chave. Guarde-a: ela só vai para o painel da Render, nunca para o repositório.

### 2. Aplicação (Render)

1. Crie uma conta em <https://render.com> e conecte o GitHub.
2. **New → Blueprint**, escolha este repositório e confirme.
3. Preencha as variáveis pedidas:
   * `BREVO_API_KEY`: a chave do passo anterior;
   * `MAIL_FROM`: `FinPilot <email-verificado@exemplo.com>`, com o remetente verificado no Brevo.
4. Aguarde o build. O endereço público aparece no topo do serviço (`https://finpilot-xxxx.onrender.com`).

O que é configurado automaticamente:

* `SECRET_KEY` é gerado pela Render;
* `BASE_URL` e `ALLOWED_HOSTS` vêm de `RENDER_EXTERNAL_URL` e `RENDER_EXTERNAL_HOSTNAME`;
* as migrations rodam no início de cada deploy (`scripts/start.sh`);
* recorrências e manutenção rodam numa thread do próprio web (`EMBEDDED_SCHEDULER=true`);
* cada push na branch `main` gera um novo deploy.

Diferenças em relação ao servidor próprio: a Render usa um único papel de banco, então a separação entre papel de migrations e papel da aplicação não existe ali. A tabela de auditoria continua protegida contra alteração pelo gatilho.

### Limites do plano gratuito

* O serviço web hiberna após cerca de 15 minutos sem acesso. O primeiro acesso seguinte leva perto de 1 minuto.
* O PostgreSQL gratuito expira 30 dias após a criação. Depois disso, é preciso migrar para um plano pago ou recriar o banco.
* O Brevo gratuito envia até 300 e-mails por dia.
* Não use dados financeiros reais em uma demo pública.

## Servidor Linux (Ubuntu 24.04 ou similar)

1. Crie um usuário não root, habilite o firewall e libere apenas SSH, 80 e 443:
   ```bash
   sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
   ```
2. Instale Docker Engine + plugin Compose (documentação oficial da Docker).
3. Aponte o DNS (registro A/AAAA) do domínio para o servidor.
4. Clone o repositório e crie `.env.production` (permissão `600`) — **nunca** versione esse arquivo:
   ```dotenv
   DOMAIN=finpilot.seudominio.com
   SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
   POSTGRES_PASSWORD=<aleatório>
   MIGRATOR_DB_PASSWORD=<aleatório>
   APP_DB_PASSWORD=<aleatório>
   REDIS_PASSWORD=<aleatório>
   SMTP_HOST=smtp.seuprovedor.com
   SMTP_PORT=587
   SMTP_USER=...
   SMTP_PASSWORD=...
   MAIL_FROM="FinPilot <no-reply@seudominio.com>"
   API_DOCS_ENABLED=false
   ```
5. Suba a stack:
   ```bash
   docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
   docker compose -f docker-compose.prod.yml ps
   curl -fsS https://$DOMAIN/health/ready
   ```
   A aplicação **recusa iniciar** se a configuração de produção for insegura (ver `docs/SECURITY.md`).

> O seed de demonstração não executa em produção (`ENV` diferente de `development`).

## Atualização (deploy de nova versão)

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d   # migrate roda antes do web
```

Migrations são versionadas; o serviço `migrate` usa o papel `finpilot_migrator` e o `web` só inicia após a conclusão bem-sucedida.

## Operação

| Tarefa | Comando |
|---|---|
| Logs (JSON, rotação 10 MB × 5) | `docker compose -f docker-compose.prod.yml logs -f web scheduler` |
| Saúde | `GET /health/live` (processo) · `GET /health/ready` (banco + Redis, 503 se degradado) |
| Gerar recorrências agora | `docker compose -f docker-compose.prod.yml run --rm web python -m app.jobs.recurrences` |
| Manutenção + retenção da auditoria (papel com privilégio) | `docker compose -f docker-compose.prod.yml run --rm -e DATABASE_URL=postgresql+psycopg://finpilot_migrator:$MIGRATOR_DB_PASSWORD@postgres:5432/finpilot web python -m app.jobs.maintenance` |
| Correlacionar um erro | o usuário recebe o `request_id`; busque-o nos logs |
| Rotação da `SECRET_KEY` | troque no `.env.production` e reinicie: sessões e tokens CSRF/links anteriores deixam de valer (usuários fazem login de novo) |

## Backup e restauração

* **Backup** (formato custom, verificado com `pg_restore --list`, retenção configurável `KEEP`, padrão 14):
  ```bash
  ./scripts/backup.sh docker-compose.prod.yml
  # cron diário às 03:00:
  # 0 3 * * * cd /opt/finpilot && ./scripts/backup.sh docker-compose.prod.yml >> backups/backup.log 2>&1
  ```
  Copie os arquivos de `backups/` para armazenamento externo criptografado (ex.: bucket com versionamento).
* **Restauração** (interrompe web/scheduler, pede confirmação digitada, reaplica permissões mínimas):
  ```bash
  ./scripts/restore.sh backups/finpilot-AAAAMMDDTHHMMSSZ.dump docker-compose.prod.yml
  ```
* **Teste de restauração** periódico em banco descartável:
  ```bash
  docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE restore_check"
  docker compose exec -T postgres pg_restore -U postgres -d restore_check --no-owner < backups/<arquivo>.dump
  docker compose exec -T postgres psql -U postgres -d restore_check -c "select count(*) from transactions"
  docker compose exec -T postgres psql -U postgres -c "DROP DATABASE restore_check"
  ```
  Esse procedimento foi executado no ambiente de desenvolvimento: as contagens do banco restaurado conferiram com as do original.

## Desenvolvimento local

```bash
make setup         # uv sync + .env + binário Tailwind (em Windows, baixe tailwindcss-windows-x64.exe para bin/)
make infra         # PostgreSQL, banco de testes, Redis e Mailpit (http://localhost:8025)
make migrate seed  # migrations + cenário de demonstração
make run           # http://localhost:8000
# ou tudo em containers:
make up
```
