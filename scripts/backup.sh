#!/usr/bin/env sh
# Backup lógico (pg_dump formato custom) do banco do FinPilot.
# Uso: ./scripts/backup.sh [arquivo-compose]   (padrão: docker-compose.yml)
# Retenção: mantém os últimos $KEEP arquivos (padrão 14).
set -eu

COMPOSE_FILE="${1:-docker-compose.yml}"
KEEP="${KEEP:-14}"
DIR="${BACKUP_DIR:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$DIR/finpilot-$STAMP.dump"

mkdir -p "$DIR"
umask 077
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  pg_dump -U postgres -d finpilot --format=custom --no-owner --no-privileges > "$FILE"

# Verificação: o arquivo precisa ser legível pelo pg_restore.
docker compose -f "$COMPOSE_FILE" exec -T postgres pg_restore --list < "$FILE" > /dev/null
echo "Backup gerado: $FILE ($(wc -c < "$FILE") bytes)"

ls -1t "$DIR"/finpilot-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
