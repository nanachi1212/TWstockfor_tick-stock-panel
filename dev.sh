#!/usr/bin/env bash
# Nanachi 的台股監控看板 — 一鍵啟動前後端
#
# 用法:
#   ./dev.sh                          # 默認 backend:3018  frontend:3011
#   BACKEND_PORT=8000 ./dev.sh        # 改後端端口
#   FRONTEND_PORT=5173 ./dev.sh       # 改前端端口
#
# Ctrl-C 同時關閉兩端。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"

# Read only the launcher-owned keys from .env. Do not source the whole file:
# .env is data, not a shell script, and may contain values that are unsafe or
# invalid as Bash syntax. Exported environment variables keep highest priority.
read_dotenv_value() {
  local key="$1"
  if [[ ! -f "$ROOT/.env" ]]; then
    return 0
  fi
  awk -v wanted="$key" '
    $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
      sub(/^[^=]*=/, "")
      sub(/[[:space:]]+#.*$/, "")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "")
      if (($0 ~ /^".*"$/) || ($0 ~ /^\047.*\047$/)) {
        $0 = substr($0, 2, length($0) - 2)
      }
      print
      exit
    }
  ' "$ROOT/.env"
}

ENV_HOST="$(read_dotenv_value HOST)"
ENV_PORT="$(read_dotenv_value PORT)"
BACKEND_HOST="${HOST:-${ENV_HOST:-0.0.0.0}}"
# Keep BACKEND_PORT as a backwards-compatible explicit override.
BACKEND_PORT="${BACKEND_PORT:-${PORT:-${ENV_PORT:-3018}}}"
FRONTEND_PORT="${FRONTEND_PORT:-3011}"
UVICORN_ENV_ARGS=()
if [[ -f "$ROOT/.env" ]]; then
  UVICORN_ENV_ARGS=(--env-file "$ROOT/.env")
fi
DISPLAY_HOST="$BACKEND_HOST"
if [[ "$DISPLAY_HOST" == "0.0.0.0" || "$DISPLAY_HOST" == "::" ]]; then
  DISPLAY_HOST="localhost"
fi

# Match Docker's BACKEND_EXTRAS behavior so old CPUs can select Polars'
# rtcompat runtime before the backend starts. An exported value wins over .env.
if [[ -z "${BACKEND_EXTRAS+x}" && -f "$ROOT/.env" ]]; then
  BACKEND_EXTRAS="$(read_dotenv_value BACKEND_EXTRAS)"
fi
BACKEND_EXTRAS="${BACKEND_EXTRAS:-}"
BACKEND_EXTRA_ARGS=()
if [[ -n "$BACKEND_EXTRAS" ]]; then
  read -r -a backend_extras <<< "$BACKEND_EXTRAS"
  for extra in "${backend_extras[@]}"; do
    BACKEND_EXTRA_ARGS+=(--extra "$extra")
  done
fi

BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
GRAY='\033[0;90m'
NC='\033[0m'

info()  { echo -e "${GRAY}[dev]${NC} $*"; }
ok()    { echo -e "${GREEN}[dev]${NC} $*"; }
warn()  { echo -e "${YELLOW}[dev]${NC} $*"; }
err()   { echo -e "${RED}[dev]${NC} $*" >&2; }

# ===== 1. 依賴檢查 =====
require_cmd() {
  local cmd="$1" hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "$cmd 未安裝"
    echo "       安裝方式:$hint"
    exit 1
  fi
}

require_cmd uv   "curl -LsSf https://astral.sh/uv/install.sh | sh"
require_cmd pnpm "npm i -g pnpm   或   corepack enable && corepack prepare pnpm@9 --activate"

# ===== 2. 端口佔用檢查 —— 佔用就直接 kill =====
free_port() {
  local name="$1" port="$2"
  local pids
  pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -z "$pids" ]; then
    return 0
  fi
  warn "端口 $port($name)被佔用,kill 現有進程 PID: $(echo "$pids" | xargs)"
  # 先 TERM
  echo "$pids" | xargs kill 2>/dev/null || true
  sleep 1
  # 還活著就 KILL
  pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    warn "TERM 沒殺掉,改用 KILL -9"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
  # 再確認一次
  pids=$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    err "端口 $port 仍被佔用 — kill 失敗。請手動處理:lsof -i :$port"
    exit 1
  fi
  ok "端口 $port 已釋放"
}
free_port backend  "$BACKEND_PORT"
free_port frontend "$FRONTEND_PORT"

# ===== 3. 依賴安裝 =====
if [ ! -d "$BACKEND_DIR/.venv" ] || [ "${#BACKEND_EXTRA_ARGS[@]}" -gt 0 ]; then
  if [ "${#BACKEND_EXTRA_ARGS[@]}" -gt 0 ]; then
    info "同步後端 Python 依賴，extras: $BACKEND_EXTRAS"
  else
    info "後端首次啟動 — 安裝 Python 依賴(約 1-2 分鐘)..."
  fi
  # macOS 自帶 bash 3.2 在 set -u 下展開空數組會報 unbound variable,
  # ${arr[@]+"${arr[@]}"} 守衛:數組為空時展開為零個參數,非空時逐個帶引號展開。
  ( cd "$BACKEND_DIR" && uv sync --frozen ${BACKEND_EXTRA_ARGS[@]+"${BACKEND_EXTRA_ARGS[@]}"} )
  ok "後端依賴裝好了"
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  info "前端首次啟動 — 安裝 Node 依賴..."
  ( cd "$FRONTEND_DIR" && pnpm install )
  ok "前端依賴裝好了"
fi

# ===== 4. 啟動 + 日誌前綴 =====
PIDS=()

cleanup() {
  echo
  info "關閉服務..."
  for pid in "${PIDS[@]:-}"; do
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  # 等子進程退出,避免孤兒
  wait 2>/dev/null || true
  ok "已退出"
  exit 0
}
trap cleanup INT TERM

# 用 awk 加前綴(macOS sed 沒有 -u line-buffered,改用 awk + fflush 兼容)
prefix_awk() {
  awk -v p="$1" '{ print p $0; fflush() }'
}

echo
echo -e "${BLUE}╭──────────────────────────────────────────────╮${NC}"
echo -e "${BLUE}│${NC}  ${GREEN}Nanachi 的台股監控看板${NC}                      ${BLUE}│${NC}"
echo -e "${BLUE}│${NC}                                              ${BLUE}│${NC}"
echo -e "${BLUE}│${NC}  backend   ${YELLOW}http://$DISPLAY_HOST:$BACKEND_PORT${NC}          ${BLUE}│${NC}"
echo -e "${BLUE}│${NC}  frontend  ${YELLOW}http://$DISPLAY_HOST:$FRONTEND_PORT${NC}          ${BLUE}│${NC}"
echo -e "${BLUE}│${NC}                                              ${BLUE}│${NC}"
echo -e "${BLUE}│${NC}  Ctrl-C 同時關閉兩端                          ${BLUE}│${NC}"
echo -e "${BLUE}╰──────────────────────────────────────────────╯${NC}"
echo

(
  cd "$BACKEND_DIR"
  # --no-sync: 跳過依賴解析, 直接用已安裝的 .venv。
  # 比 --frozen 更徹底: 不校驗 lockfile, 避免鏡像源 403/網絡抖動導致後端起不來。
  # python -m uvicorn: 強制用 venv 的解釋器和 uvicorn 模塊, 防止 PATH 裡
  # 其他 Python(如 /usr/local/bin/uvicorn) 搶先, 導致用錯誤版本啟動後端。
  uv run --no-sync python -m uvicorn app.main:app ${UVICORN_ENV_ARGS[@]+"${UVICORN_ENV_ARGS[@]}"} --reload \
    --host "$BACKEND_HOST" --port "$BACKEND_PORT" 2>&1 \
    | prefix_awk "$(printf "${BLUE}[backend ]${NC} ")"
) &
PIDS+=("$!")

(
  cd "$FRONTEND_DIR"
  BACKEND_HOST="$BACKEND_HOST" BACKEND_PORT="$BACKEND_PORT" \
    pnpm dev --host "$BACKEND_HOST" --port "$FRONTEND_PORT" 2>&1 \
    | prefix_awk "$(printf "${GREEN}[frontend]${NC} ")"
) &
PIDS+=("$!")

# 等任一退出(bash 4.3+)或全部退出(老 bash)
if wait -n 2>/dev/null; then
  warn "其中一個進程退出,正在關閉另一個..."
  cleanup
else
  # 老 bash 沒有 wait -n,退化為 wait 全部
  wait
fi
