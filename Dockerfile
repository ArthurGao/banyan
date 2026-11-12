# syntax=docker/dockerfile:1
# Banyan in one image (design §12 B6, deploy/README.md): the built frontend served by the API
# process, which also runs the job worker. Settings and secrets come from the environment
# (backend/.env through compose); nothing secret is in the image.
#
#   docker build -t banyan .                    # the app: backend + built frontend
#   docker build --target backend -t banyan .   # the API alone (no frontend build needed)

FROM node:22.23-slim AS frontend
WORKDIR /frontend
# npm, not corepack: an older corepack lacked npm's current signing keys and refused pnpm.
RUN npm install --global pnpm@10.34.7
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/.npmrc ./
RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# Dependencies are built here; the runtime below gets the virtualenv, not git or uv.
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /bin/uv
# git: the llm-steward SDK is a pinned git dependency (backend/pyproject.toml).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

FROM python:3.12-slim AS backend
RUN useradd --create-home --uid 10001 banyan
WORKDIR /app
COPY --from=builder /app /app
USER banyan
ENV PATH=/app/.venv/bin:$PATH
EXPOSE 8800
CMD ["sh", "-c", "banyan migrate && banyan serve --host 0.0.0.0"]

FROM backend AS app
COPY --from=frontend /frontend/dist /app/frontend-dist
ENV BANYAN_FRONTEND_DIST=/app/frontend-dist
