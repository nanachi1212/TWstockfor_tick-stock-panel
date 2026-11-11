# 兩階段建置:前端 dist 複製進後端映像檔,單一容器執行
ARG BACKEND_EXTRAS=
ARG CODEX_CLI_VERSION=0.144.3

# === Stage 1: 前端建置 ===
FROM node:22-alpine AS frontend-builder
WORKDIR /build
RUN npm install -g pnpm@9
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install
COPY frontend/ ./
RUN pnpm build

# === Stage 1c: Codex CLI ===
# 固定版本讓映像檔可重現；只複製安裝產物到執行映像檔，不保留 npm。
FROM node:22-bookworm-slim AS codex-builder
# 版本由頂層 ARG CODEX_CLI_VERSION 提供, 這裡僅宣告以繼承, 不再重複預設值。
ARG CODEX_CLI_VERSION
RUN npm install --global --prefix /opt/codex "@openai/codex@${CODEX_CLI_VERSION}" \
    && CODEX_NATIVE="$(find /opt/codex -type f -path '*/vendor/*/bin/codex' -print -quit)" \
    && test -n "$CODEX_NATIVE" \
    && cp "$CODEX_NATIVE" /opt/codex-native \
    && chmod +x /opt/codex-native \
    && /opt/codex-native --version

# === Stage 2: Python 執行環境 ===
FROM python:3.12-slim AS runtime
ARG BACKEND_EXTRAS=
WORKDIR /app

# 安裝 uv(快)
RUN pip install --no-cache-dir uv

# Backend deps
COPY README.md /README.md
COPY backend/pyproject.toml backend/uv.lock* ./
RUN set -- --no-dev; \
    for extra in $BACKEND_EXTRAS; do \
      set -- "$@" --extra "$extra"; \
    done; \
    uv sync --frozen "$@" || uv sync "$@"

# Backend code
# 注意:Docker 裡 WORKDIR=/app, 而 config.py 的 _PROJECT_ROOT 是按開發佈局
# (<root>/backend/app/) 推導的, 容器內會錯算到 /。這裡用環境變數明確指定
# 關鍵路徑, 確保 static / data 都指向容器內正確位置。
COPY VERSION /app/VERSION
COPY backend/app ./app
ENV STATIC_DIR=/app/static \
    DATA_DIR=/app/data \
    TICKFLOW_ENV_FILE=/app/.env

# 前端靜態檔
COPY --from=frontend-builder /build/dist ./static

# Codex CLI 使用官方 npm 套件附帶的當前平台原生執行檔，執行時不需要 Node.js。
COPY --from=codex-builder /opt/codex-native /usr/local/bin/codex
RUN codex --version

ENV PYTHONPATH=/app
# 預設時區: 交易時段判斷已在程式裡明確使用台北時間 (app/market_time.py),
# 此處讓記錄檔時間戳等其餘 naive 時間也對齊台北時間。
ENV TZ=Asia/Taipei
EXPOSE 3018
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3018"]
