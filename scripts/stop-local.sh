#!/usr/bin/env bash
# =============================================================================
# AgentCore 一键停止脚本（Linux / macOS）
#
#   1. 按端口停止后端 uvicorn（默认 8000）
#   2. 按端口停止前端 vite dev server（默认 3000）
#   3. 兜底清理残留的 uvicorn / spawn worker 进程
#
# lsof 会列出所有共享该 LISTEN socket 的进程（含 uvicorn --reload 派生的
# worker），因此逐端口 kill 即可一并清除父进程与子 worker。
#
# 用法:
#   bash scripts/stop-local.sh                    # 停止后端 + 前端
#   bash scripts/stop-local.sh --backend-only     # 只停后端
#   bash scripts/stop-local.sh --frontend-only    # 只停前端
#   bash scripts/stop-local.sh --backend-port 8001
# =============================================================================
set -uo pipefail

BACKEND_PORT=8000
FRONTEND_PORT=3000
BACKEND_ONLY=false
FRONTEND_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend-port)  BACKEND_PORT="$2";  shift 2 ;;
        --frontend-port) FRONTEND_PORT="$2"; shift 2 ;;
        --backend-only)  BACKEND_ONLY=true;  shift   ;;
        --frontend-only) FRONTEND_ONLY=true; shift   ;;
        -h|--help)
            sed -n '2,17p' "$0"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

log_step() { printf '\033[36m[agentcore]\033[0m %s\n' "$1"; }
log_ok()   { printf '\033[32m[agentcore]\033[0m %s\n' "$1"; }
log_warn() { printf '\033[33m[agentcore]\033[0m %s\n' "$1"; }

# 列出监听某端口的所有进程 PID（lsof 优先，fuser 回退）。
pids_on_port() {
    local port="$1" pids=""
    if command -v lsof &>/dev/null; then
        pids="$(lsof -nP -ti TCP:"$port" -s TCP:LISTEN 2>/dev/null || true)"
    fi
    if [[ -z "$pids" ]] && command -v fuser &>/dev/null; then
        pids="$(fuser "$port"/tcp 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true)"
    fi
    printf '%s' "$pids"
}

kill_by_port() {
    local port="$1" label="$2" pids
    pids="$(pids_on_port "$port")"
    if [[ -z "$pids" ]]; then
        log_step "$label 端口 $port 无监听进程，跳过"
        return
    fi
    # 先杀子进程，再杀本体
    for pid in $pids; do
        pkill -P "$pid" 2>/dev/null || true
    done
    for pid in $pids; do
        if kill -TERM "$pid" 2>/dev/null; then
            log_ok "已停止 $label (PID $pid)"
        fi
    done
}

log_step "开始停止 AgentCore 服务 ..."

if ! $FRONTEND_ONLY; then
    kill_by_port "$BACKEND_PORT" "后端"
fi
if ! $BACKEND_ONLY; then
    kill_by_port "$FRONTEND_PORT" "前端"
fi

# 兜底：清理未占用端口但残留的 uvicorn 进程（含 spawn worker）
if ! $FRONTEND_ONLY; then
    if pkill -9 -f "uvicorn agentcore" 2>/dev/null; then
        log_ok "已兜底清理残留 uvicorn agentcore 进程"
    fi
fi

log_ok "停止完成。"
