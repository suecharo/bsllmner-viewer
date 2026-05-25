ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.5.11

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-bookworm

LABEL org.opencontainers.image.title="bsllmner-viewer" \
      org.opencontainers.image.description="Viewer for bsllmner-mk2 BioSample x ontology mapping results" \
      org.opencontainers.image.authors="Hirotaka Suetake" \
      org.opencontainers.image.url="https://github.com/suecharo/bsllmner-viewer" \
      org.opencontainers.image.source="https://github.com/suecharo/bsllmner-viewer" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      curl \
      jq \
      less \
      vim-tiny && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/app/.venv/bin:$PATH

# Dependency layer (cache-friendly). uv.lock is optional on first build;
# `uv sync` (without --locked) will create it when missing.
COPY pyproject.toml .python-version ./
COPY uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project && \
    chmod -R a+rwX /app/.venv

COPY . .

CMD ["sleep", "infinity"]
