# One image for web, bot, worker and news. The service is the command: `saley <service>`.
# Stage web builds the dashboard v2 with Node (D144, D145); stage build makes the virtualenv
# with uv from the lock file; stage run runs it as a non-root user. The run image has no Node.

# Node 24 LTS, pinned by the multi-arch index digest (24-bookworm-slim, pushed 2026-10-14).
# npm ci installs exactly package-lock.json with install scripts off (web/.npmrc too).
FROM node:24-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.1-python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS run
RUN useradd --create-home --uid 10001 saley
WORKDIR /app
COPY --from=build --chown=saley:saley /app/.venv /app/.venv
COPY --chown=saley:saley src ./src
COPY --from=web --chown=saley:saley /app/src/saley/web/dist ./src/saley/web/dist
COPY --chown=saley:saley alembic.ini ./
COPY --chown=saley:saley migrations ./migrations
COPY --chown=saley:saley docs/DECISIONS.md docs/goal-deliverable-map.yaml ./docs/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER saley
EXPOSE 8090
CMD ["saley", "web"]
