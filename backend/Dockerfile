# ---- 前端构建 ----
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---- 后端运行时 ----
FROM python:3.12-slim AS runtime
WORKDIR /app/backend

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
# 前端构建产物放进 app/static，由 SPA 托管逻辑提供
COPY --from=frontend-build /app/frontend/dist ./app/static

ENV PATH="/app/backend/.venv/bin:$PATH"
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8080"]
