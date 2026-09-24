# Two stages so the runtime image carries the virtualenv but not uv, the lock
# file resolution, or the build cache.
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.4 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies change far less often than source, so they get their own layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev


FROM python:3.11-slim AS runtime

# curl is the readiness probe used by Compose and Kubernetes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# The relay writes a SQLite file unless RELAY_DATABASE_URL points elsewhere, so
# it must not run as root with the database inside the image layer.
RUN useradd --create-home --uid 10001 relay \
    && mkdir -p /data \
    && chown relay:relay /data

WORKDIR /app
COPY --from=builder --chown=relay:relay /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    RELAY_DATABASE_URL="sqlite:////data/agent-relay.db"

USER relay
VOLUME ["/data"]
EXPOSE 8000

# --host 0.0.0.0 is required: uvicorn's default 127.0.0.1 is unreachable from
# outside the container, which makes `docker run -p` look broken.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
