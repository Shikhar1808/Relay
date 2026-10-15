# Build stage: resolve dependencies into a virtualenv with uv.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Runtime stage: no uv, no build tools, not root. No volume: the records are in Postgres.
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home fulfilment
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY app ./app

# No .pyc writes, so the container also runs with a read-only root filesystem.
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001

# 8000: /mcp and /healthz. Never published or put behind the Ingress: only the gateway calls it.
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status != 200)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
