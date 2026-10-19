#!/bin/sh
# Executado uma única vez na criação do volume do PostgreSQL.
# Cria dois papéis com privilégios mínimos:
#   - finpilot_migrator: dono do schema, aplica migrations (DDL)
#   - finpilot_app:      usado pela aplicação; apenas DML nas tabelas, sem DDL
set -eu

: "${APP_DB_NAME:?}" "${MIGRATOR_DB_PASSWORD:?}" "${APP_DB_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE ROLE finpilot_migrator LOGIN PASSWORD '${MIGRATOR_DB_PASSWORD}';
  CREATE ROLE finpilot_app LOGIN PASSWORD '${APP_DB_PASSWORD}';
  CREATE DATABASE ${APP_DB_NAME} OWNER finpilot_migrator;
  REVOKE ALL ON DATABASE ${APP_DB_NAME} FROM PUBLIC;
  GRANT CONNECT ON DATABASE ${APP_DB_NAME} TO finpilot_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$APP_DB_NAME" <<-SQL
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
  REVOKE CREATE ON SCHEMA public FROM PUBLIC;
  ALTER SCHEMA public OWNER TO finpilot_migrator;
  GRANT USAGE ON SCHEMA public TO finpilot_app;
  ALTER DEFAULT PRIVILEGES FOR ROLE finpilot_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO finpilot_app;
  ALTER DEFAULT PRIVILEGES FOR ROLE finpilot_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO finpilot_app;
SQL
