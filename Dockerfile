# 兩階段構建:前端 dist 拷進後端鏡像,單容器運行
# 可選:構建網絡無法直連官方源時,傳入 --build-arg USE_CN_MIRROR=1 啟用國內鏡像
ARG USE_CN_MIRROR=1
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# 備用 PyPI 源:主源同步延遲/故障時自動兜底(阿里雲與清華互為補充)
ARG PYPI_FALLBACK=https://mirrors.aliyun.com/pypi/simple
ARG BACKEND_EXTRAS=
ARG CODEX_CLI_VERSION=0.144.3

# === Stage 1: 前端構建 ===
FROM node:20-alpine AS frontend-builder
ARG USE_CN_MIRROR=1
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /build
# 關鍵:corepack 不讀 npm 的 registry 配置,且跨 RUN 不保留環境變量,
# 因此國內網絡下最穩的做法是直接用 npm 安裝 pnpm(npm 會讀取 .npmrc 鏡像源),
# 徹底繞開 corepack 再次聯網下載 pnpm 的問題。
RUN if [ "$USE_CN_MIRROR" = "1" ]; then npm config set registry "$NPM_REGISTRY"; fi && \
    npm install -g pnpm@9
# 讓 pnpm 走鏡像源安裝依賴
RUN if [ "$USE_CN_MIRROR" = "1" ]; then pnpm config set registry "$NPM_REGISTRY"; fi
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install
COPY frontend/ ./
RUN pnpm build

# === Stage 1c: Codex CLI ===
# 固定版本保證鏡像可復現；只複製安裝產物到運行鏡像，不保留 npm。
FROM node:20-bookworm-slim AS codex-builder
ARG USE_CN_MIRROR=1
ARG NPM_REGISTRY=https://registry.npmmirror.com
# 版本由頂層 ARG CODEX_CLI_VERSION 提供, 這裡僅聲明以繼承, 不再重複默認值。
ARG CODEX_CLI_VERSION
RUN if [ "$USE_CN_MIRROR" = "1" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm install --global --prefix /opt/codex "@openai/codex@${CODEX_CLI_VERSION}" \
    && CODEX_NATIVE="$(find /opt/codex -type f -path '*/vendor/*/bin/codex' -print -quit)" \
    && test -n "$CODEX_NATIVE" \
    && cp "$CODEX_NATIVE" /opt/codex-native \
    && chmod +x /opt/codex-native \
    && /opt/codex-native --version

# === Stage 2: Python 運行時 ===
FROM python:3.11-slim AS runtime
ARG USE_CN_MIRROR=1
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PYPI_FALLBACK=https://mirrors.aliyun.com/pypi/simple
ARG BACKEND_EXTRAS=
WORKDIR /app

# 安裝 uv(快) —— 國內鏡像下三重兜底:主源 → 備用源 → 官方源,
# 任一成功即可,避免單一鏡像同步延遲/故障導致構建失敗。
# uv 發版極頻繁,國內鏡像同步存在時間窗口,不鎖版本且無 fallback 時
# 容易遇到 "from versions: none"(索引解析不到最新版)。
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      pip install --no-cache-dir uv -i "$PYPI_INDEX" || \
      pip install --no-cache-dir uv -i "$PYPI_FALLBACK" || \
      pip install --no-cache-dir uv; \
    else \
      pip install --no-cache-dir uv; \
    fi

# Backend deps
COPY README.md /README.md
COPY backend/pyproject.toml backend/uv.lock* ./
# uv 原生支持同時掛多個 index(主源 + 備用源),會自動在兩源中查找,
# 比逐個重試更穩健 —— 任一源缺包時另一源補位。
RUN if [ "$USE_CN_MIRROR" = "1" ]; then \
      export UV_DEFAULT_INDEX="$PYPI_INDEX" UV_EXTRA_INDEX_URL="$PYPI_FALLBACK"; \
    fi; \
    set -- --no-dev; \
    for extra in $BACKEND_EXTRAS; do \
      set -- "$@" --extra "$extra"; \
    done; \
    uv sync --frozen "$@" || uv sync "$@"

# Backend code
# 注意:Docker 裡 WORKDIR=/app, 而 config.py 的 _PROJECT_ROOT 是按開發佈局
# (<root>/backend/app/) 推導的, 容器內會錯算到 /。這裡用環境變量顯式指定
# 關鍵路徑, 確保 static / data 都指向容器內正確位置。
COPY VERSION /app/VERSION
COPY backend/app ./app
ENV STATIC_DIR=/app/static \
    DATA_DIR=/app/data \
    TICKFLOW_ENV_FILE=/app/.env

# Frontend 靜態產物
COPY --from=frontend-builder /build/dist ./static

# Codex CLI 使用官方 npm 包攜帶的當前平台原生二進制，無需運行時 Node.js。
COPY --from=codex-builder /opt/codex-native /usr/local/bin/codex
RUN codex --version

ENV PYTHONPATH=/app
# 兜底時區: 交易時段判斷已在代碼裡顯式用北京時間 (app/market_time.py),
# 此處讓日誌時間戳等其餘 naive 時間也對齊北京時間。
ENV TZ=Asia/Shanghai
EXPOSE 3018
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3018"]
