# syntax=docker/dockerfile:1.7
# Imagem de produção do FinPilot: multi-stage, usuário sem privilégios, sem compiladores.

# ---------------------------------------------------------------- 1. CSS (Tailwind standalone, sem Node)
FROM debian:bookworm-slim AS css
ARG TAILWIND_VERSION=v4.1.11
ARG TAILWIND_SHA256=64805b84af4292e043ea6f86d242f191c0ac75359c1a498455dfe6c642afdbab
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /usr/local/bin/tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64" \
    && echo "${TAILWIND_SHA256}  /usr/local/bin/tailwindcss" | sha256sum -c - \
    && chmod +x /usr/local/bin/tailwindcss
WORKDIR /src
COPY app/static/css/input.css app/static/css/input.css
COPY app/templates app/templates
COPY app/web app/web
RUN tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify

# ---------------------------------------------------------------- 2. Dependências Python
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- 3. Runtime
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENV=production
RUN groupadd --system --gid 10001 finpilot \
    && useradd --system --uid 10001 --gid finpilot --home-dir /app --shell /usr/sbin/nologin finpilot
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=finpilot:finpilot alembic.ini ./
COPY --chown=finpilot:finpilot alembic ./alembic
COPY --chown=finpilot:finpilot scripts ./scripts
COPY --chown=finpilot:finpilot app ./app
COPY --from=css --chown=finpilot:finpilot /src/app/static/css/app.css ./app/static/css/app.css
USER finpilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).status == 200 else 1)"
# Servidor de produção: Uvicorn com múltiplos workers, sem --reload; atrás do proxy (Caddy).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header", "--no-access-log"]
