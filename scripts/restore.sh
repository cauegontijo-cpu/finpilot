#!/usr/bin/env sh
# Restaura um backup gerado por scripts/backup.sh.
# ATENÇÃO: substitui o conteúdo atual do banco. Pare a aplicação antes (web + scheduler).
# Uso: ./scripts/restore.sh backups/finpilot-AAAAMMDDTHHMMSSZ.dump [arquivo-compose]
set -eu

FILE="${1:?informe o arquivo .dump}"
COMPOSE_FILE="${2:-docker-compose.yml}"
[ -f "$FILE" ] || { echo "Arquivo não encontrado: $FILE"; exit 1; }

printf "Restaurar %s sobre o banco atual? Digite RESTAURAR para confirmar: " "$FILE"
read -r answer
[ "$answer" = "RESTAURAR" ] || { echo "Cancelado."; exit 1; }

docker compose -f "$COMPOSE_FILE" stop web scheduler || true
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  pg_restore -U postgres -d finpilot --clean --if-exists --no-owner --role=finpilot_migrator < "$FILE"
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U postgres -d finpilot -v ON_ERROR_STOP=1 -c \
  "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO finpilot_app;
   REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM finpilot_app;"
docker compose -f "$COMPOSE_FILE" start web scheduler
echo "Restauração concluída. Verifique /health/ready e um login de teste."
