FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@10.18.3 --activate && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN npm install --prefix /opt/exports pptxgenjs@4.0.1
ENV NODE_PATH=/opt/exports/node_modules
COPY pyproject.toml requirements.lock ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY --from=web /web/dist ./frontend/dist
RUN useradd --create-home app && mkdir -p /app/.local && chown -R app:app /app
USER app
ENV BRIEFFORGE_DATA_DIR=/app/.local
EXPOSE 8788
CMD ["briefforge", "serve", "--host", "0.0.0.0"]

