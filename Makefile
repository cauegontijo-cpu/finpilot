# FinPilot — atalhos de desenvolvimento e operação.
# Requer: uv, Docker Compose. Em Windows, use Git Bash ou WSL para executar o make.

TAILWIND ?= ./bin/tailwindcss
TAILWIND_VERSION ?= v4.1.11
COMPOSE ?= docker compose

.PHONY: help setup infra css run migrate seed seed-reset test test-fast cov lint format typecheck check e2e screenshots \
        up down logs recurrences maintenance backup restore

help: ## Lista os comandos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Instala dependências e o binário do Tailwind
	uv sync
	@test -f .env || (cp .env.example .env && echo ">> .env criado a partir do exemplo: troque os segredos!")
	@test -x $(TAILWIND) || (mkdir -p bin && curl -fsSL -o $(TAILWIND) \
		https://github.com/tailwindlabs/tailwindcss/releases/download/$(TAILWIND_VERSION)/tailwindcss-linux-x64 && chmod +x $(TAILWIND))

infra: ## Sobe PostgreSQL, banco de testes, Redis e Mailpit
	$(COMPOSE) up -d postgres db-test redis mailpit

css: ## Compila o Tailwind (stdout evita bug do binário com caminhos não ASCII)
	$(TAILWIND) -i app/static/css/input.css --minify > app/static/css/app.css

run: css ## Servidor local com recarregamento (desenvolvimento)
	uv run uvicorn app.main:app --reload --reload-dir app

migrate: ## Aplica as migrations
	uv run alembic upgrade head

seed: ## Cria o cenário de demonstração (somente ENV=development)
	uv run python -m scripts.seed_demo

seed-reset: ## Recria o cenário de demonstração
	uv run python -m scripts.seed_demo --reset

test: ## Suíte completa (unitários, integração, segurança e concorrência)
	uv run pytest

test-fast: ## Apenas testes unitários
	uv run pytest tests/unit -q

cov: ## Cobertura (falha abaixo de 80% em services/domain)
	uv run pytest --cov=app/services --cov=app/domain --cov-report=term-missing --cov-fail-under=80

lint: ## Ruff (lint + formatação em modo verificação)
	uv run ruff check app tests scripts
	uv run ruff format --check app tests scripts

format: ## Formata o código
	uv run ruff format app tests scripts
	uv run ruff check --fix app tests scripts

typecheck: ## mypy --strict
	uv run mypy

check: lint typecheck test ## Tudo que o CI executa

e2e: ## Testes ponta a ponta (requer app rodando em E2E_BASE_URL)
	uv run pytest tests/e2e -m e2e

screenshots: ## Screenshots reais (requer app rodando + seed)
	uv run python -m scripts.screenshots --out docs/screenshots

up: ## Stack completa de desenvolvimento em containers
	$(COMPOSE) build web
	$(COMPOSE) up -d

down: ## Derruba a stack (mantém volumes)
	$(COMPOSE) down

logs: ## Acompanha os logs da aplicação
	$(COMPOSE) logs -f web scheduler

recurrences: ## Executa o job de recorrências uma vez
	uv run python -m app.jobs.recurrences

maintenance: ## Executa a limpeza periódica uma vez
	uv run python -m app.jobs.maintenance

backup: ## Backup lógico do banco (ver docs/DEPLOYMENT.md)
	./scripts/backup.sh

restore: ## Restaura um backup: make restore FILE=backups/arquivo.dump
	./scripts/restore.sh $(FILE)
