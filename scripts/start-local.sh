#!/usr/bin/env bash
# =============================================================================
# AgentCore 一键启动脚本（Linux / macOS）
#
#   1. 检查 Python 环境（自动创建 .venv）
#   2. 校验 deepagents 本地路径存在
#   3. 检查前端依赖（console/node_modules 不存在则 npm install）
#   4. 启动后端 uvicorn（后台，--workers 1）
#   5. 启动前端 vite dev server（后台；存在 console/dist 时走生产模式跳过）
#   6. 轮询 /health 等待就绪
#   7. 打开浏览器并打印入口
#
# 用法:
#   bash scripts/start-local.sh                  # 一键启动
#   bash scripts/start-local.sh --no-browser     # 不自动打开浏览器
#   bash scripts/start-local.sh --install-dev    # 安装含 pytest 的开发依赖
#   bash scripts/start-local.sh --host 0.0.0.0   # 指定后端监听地址
#   bash scripts/start-local.sh --backend-port 8000 --frontend-port 3000
# =============================================================================
set -euo pipefail

# ── 默认参数 ────────────────────────────────────────────────────────────────
HOST_NAME="127.0.0.1"
BACKEND_PORT=8000
FRONTEND_PORT=3000
INSTALL_DEV=false
NO_BROWSER=false

# ── 参数解析 ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)           HOST_NAME="$2";      shift 2 ;;
        --backend-port)   BACKEND_PORT="$2";   shift 2 ;;
        --frontend-port)  FRONTEND_PORT="$2";  shift 2 ;;
        --install-dev)    INSTALL_DEV=true;     shift   ;;
        --no-browser)     NO_BROWSER=true;      shift   ;;
        -h|--help)
            sed -n '2,16p' "$0"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

# ── 工具函数 ────────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

log_step() {
    printf '\033[36m[agentcore]\033[0m %s\n' "$1"
}

log_ok() {
    printf '\033[32m[agentcore]\033[0m %s\n' "$1"
}

log_err() {
    printf '\033[31m[agentcore]\033[0m %s\n' "$1" >&2
}

# 跨平台打开浏览器
open_browser() {
    local url="$1"
    if command -v xdg-open &>/dev/null; then
        xdg-open "$url"
    elif command -v open &>/dev/null; then
        open "$url"
    elif command -v start &>/dev/null; then
        start "$url"
    else
        log_step "无法自动打开浏览器，请手动访问: $url"
    fi
}

# 清理后台进程
BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
    if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        log_step "停止后端进程 (PID: $BACKEND_PID)"
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        log_step "停止前端进程 (PID: $FRONTEND_PID)"
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ── 1. Python 环境检查 ──────────────────────────────────────────────────────
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    log_err "未找到 Python。请安装 Python 3.11+ 并加入 PATH。"
    exit 1
fi

VENV_DIR="$ROOT/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    log_step "创建虚拟环境 .venv ..."
    $PYTHON_CMD -m venv .venv
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
    log_err "虚拟环境 Python 不存在: $VENV_PYTHON"
    exit 1
fi
log_step "Python 环境就绪: $VENV_PYTHON"

# ── 2. deepagents 本地路径校验 ──────────────────────────────────────────────
# 按平台推断 deepagents 路径
if [[ "$(uname)" == "Darwin" ]]; then
    DEEPAGENTS_PATH="${DEEPAGENTS_PATH:-$HOME/code/deepagents/libs/deepagents}"
else
    DEEPAGENTS_PATH="${DEEPAGENTS_PATH:-$HOME/code/deepagents/libs/deepagents}"
fi

if [[ ! -d "$DEEPAGENTS_PATH" ]]; then
    log_err "deepagents 本地路径不存在: $DEEPAGENTS_PATH"
    log_err "请先克隆/检出 deepagents 仓库，或设置环境变量 DEEPAGENTS_PATH"
    exit 1
fi
log_step "deepagents 路径校验通过: $DEEPAGENTS_PATH"

# ── 3. 安装后端依赖 ─────────────────────────────────────────────────────────
log_step "安装 deepagents 本地依赖: $DEEPAGENTS_PATH ..."
$VENV_PYTHON -m pip install -q -e "$DEEPAGENTS_PATH"

log_step "安装后端依赖 (pip install -e .) ..."
if $INSTALL_DEV; then
    $VENV_PYTHON -m pip install -q -e ".[dev]"
else
    $VENV_PYTHON -m pip install -q -e .
fi

# ── 4. 前端依赖检查 + 构建（每次启动都重新 build 确保最新代码生效） ──────
CONSOLE_DIR="$ROOT/console"
CONSOLE_DIST="$CONSOLE_DIR/dist"

if ! command -v npm &>/dev/null; then
    log_err "未找到 npm。请安装 Node.js。"
    exit 1
fi

if [[ ! -d "$CONSOLE_DIR/node_modules" ]]; then
    log_step "console/node_modules 不存在，执行 npm install ..."
    (cd "$CONSOLE_DIR" && npm install)
else
    log_step "前端依赖已就绪 (console/node_modules)"
fi

# 每次启动都重新构建前端，确保最新代码生效
log_step "构建前端 (npm run build) ..."
(cd "$CONSOLE_DIR" && npm run build)
PRODUCTION_MODE=true
log_step "前端构建完成 —— 生产模式：由后端托管前端静态文件"

# ── 5. 并行启动后端 + 前端（后台进程，日志写入 logs/） ──────────────────────
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

log_step "启动后端 uvicorn (--workers 1) ..."
$VENV_PYTHON -m uvicorn agentcore.api:app \
    --host "$HOST_NAME" --port "$BACKEND_PORT" --workers 1 \
    > "$LOG_DIR/backend.out.log" 2> "$LOG_DIR/backend.err.log" &
BACKEND_PID=$!

if ! $PRODUCTION_MODE; then
    log_step "启动前端 vite dev server ..."
    (cd "$CONSOLE_DIR" && npm run dev -- --port "$FRONTEND_PORT" --strictPort) \
        > "$LOG_DIR/frontend.out.log" 2> "$LOG_DIR/frontend.err.log" &
    FRONTEND_PID=$!
fi

# ── 6. 轮询 /health 等待后端就绪（最长 90 秒） ─────────────────────────────
HEALTH_URL="http://$HOST_NAME:$BACKEND_PORT/health"
log_step "等待后端就绪: $HEALTH_URL"

READY=false
for i in $(seq 1 45); do
    sleep 2
    # 检查后端进程是否还活着
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        log_err "后端进程已退出，请查看 logs/backend.err.log:"
        tail -30 "$LOG_DIR/backend.err.log" 2>/dev/null || true
        exit 1
    fi
    if curl -sf --max-time 2 "$HEALTH_URL" > /dev/null 2>&1; then
        READY=true
        break
    fi
done

if ! $READY; then
    log_err "后端在 90 秒内未就绪，请查看 logs/backend.err.log"
    exit 1
fi
log_ok "后端已就绪 ✅"

# ── 7. 打开浏览器并打印入口 ─────────────────────────────────────────────────
if $PRODUCTION_MODE; then
    ENTRY_URL="http://$HOST_NAME:$BACKEND_PORT/"
else
    ENTRY_URL="http://localhost:$FRONTEND_PORT/"
fi

if ! $NO_BROWSER; then
    log_step "打开浏览器: $ENTRY_URL"
    open_browser "$ENTRY_URL"
fi

echo ""
printf '\033[32m%s\033[0m\n' "============================================================"
printf '\033[32m%s\033[0m\n' "  AgentCore 已启动"
printf '\033[32m%s\033[0m\n' "  前端入口   : $ENTRY_URL"
printf '\033[32m%s\033[0m\n' "  后端 API   : http://$HOST_NAME:$BACKEND_PORT  (/health, /docs)"
if $PRODUCTION_MODE; then
    printf '\033[32m%s\033[0m\n' "  托管模式   : 生产模式（后端托管 console/dist）"
else
    printf '\033[32m%s\033[0m\n' "  托管模式   : 开发模式（vite dev + API 代理）"
fi
printf '\033[32m%s\033[0m\n' "  日志目录   : $LOG_DIR"
printf '\033[32m%s\033[0m\n' "  停止服务   : kill $BACKEND_PID${FRONTEND_PID:+ $FRONTEND_PID}"
printf '\033[32m%s\033[0m\n' "============================================================"

# 保持前台等待，Ctrl+C 触发 trap 清理
log_step "按 Ctrl+C 停止所有服务"
wait
