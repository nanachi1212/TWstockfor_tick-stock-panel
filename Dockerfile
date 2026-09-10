# 兩階段構建:前端 dist 拷進後端鏡像,單容器運行
# 可選:構建網絡無法直連官方源時,傳入 --build-arg USE_CN_MIRROR=1 啟用國內鏡像
# 可選:stock-sdk 插件默認不打包(它抓取第三方財經網站接口,存在版權與反爬風險)。
#       如確需啟用,傳入 --build-arg INCLUDE_STOCKSDK=1 顯式開啟,使用風險自負。
ARG USE_CN_MIRROR=1
ARG INCLUDE_STOCKSDK=0
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

# === Stage 1b: stock-sdk 插件依賴(可選,默認跳過) ===
# ⚠️ 合規提示: stock-sdk 通過 node bridge.mjs 抓取第三方財經網站(如東方財富)的行情接口,
#    未經對方授權,可能違反其服務條款並涉及交易所行情版權。默認不打包(INCLUDE_STOCKSDK=0)。
#    如確需啟用,構建時傳 --build-arg INCLUDE_STOCKSDK=1,即視為使用者知悉並自行承擔合規責任。
# INCLUDE_STOCKSDK=0 時,本 stage 僅產出空 node_modules 目錄,保證後續 COPY 不報錯。
FROM node:20-bookworm-slim AS stocksdk-builder
ARG USE_CN_MIRROR=1
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG INCLUDE_STOCKSDK=0
WORKDIR /build
RUN if [ "$USE_CN_MIRROR" = "1" ]; then npm config set registry "$NPM_REGISTRY"; fi
COPY backend/app/plugins/stocksdk/package.json backend/app/plugins/stocksdk/package-lock.json ./
# INCLUDE_STOCKSDK=1 時安裝依賴;=0 時僅建空目錄,使最終鏡像不含 stock-sdk 依賴
RUN if [ "$INCLUDE_STOCKSDK" = "1" ]; then \
      (npm ci || npm install); \
    else \
      mkdir -p /build/node_modules; \
    fi

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
ARG INCLUDE_STOCKSDK=0
WORKDIR /app

# Node.js 運行時: 僅在啟用 stock-sdk 插件時安裝(供 node bridge.mjs 使用)。
# Codex CLI 從官方 npm 包提取原生二進制，不依賴運行時 Node.js。
# bookworm 自帶 nodejs 18.19, 滿足插件 engines>=18; --no-install-recommends 精簡,
# 自帶 libnode/libc-ares 等全部動態依賴, 無需手動補庫。
# 國內構建走 apt mirror 已在 debian 鏡像sources.list 配好, 無需額外換源。
# tesseract-ocr: 自選截圖導入（始終安裝）; nodejs: 僅 INCLUDE_STOCKSDK=1 時安裝
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && if [ "$INCLUDE_STOCKSDK" = "1" ]; then \
         apt-get install -y --no-install-recommends nodejs \
         && node --version; \
       fi \
    && rm -rf /var/lib/apt/lists/* \
    && tesseract --version

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
# 三個關鍵路徑, 確保 static / tiers / data 都指向容器內正確位置。
COPY VERSION /app/VERSION
COPY backend/app ./app
# stock-sdk 插件依賴: 從 stocksdk-builder 拷入。
# INCLUDE_STOCKSDK=0(默認) 時, stocksdk-builder 產出空目錄,此處拷入空目錄,
# 即最終鏡像不含 stock-sdk 依賴,插件默認不可用。
# COPY --from 不受 .dockerignore 的 **/node_modules 規則影響。
COPY --from=stocksdk-builder /build/node_modules ./app/plugins/stocksdk/node_modules
COPY tiers.yaml /app/tiers.yaml
ENV STATIC_DIR=/app/static \
    TIERS_YAML=/app/tiers.yaml \
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
