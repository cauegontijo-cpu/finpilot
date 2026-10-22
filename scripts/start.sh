#!/bin/sh
# Inicialização em produção: aplica migrations (idempotente) e sobe o Uvicorn sem --reload.
# Usado pelo Dockerfile; a porta vem de $PORT (Render, Railway etc.) ou 8000.
set -eu

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers \
  --forwarded-allow-ips "*" \
  --no-server-header \
  --no-access-log
