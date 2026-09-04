#!/usr/bin/env bash
# =============================================================================
# OpenSandbox 服务器本地快速启动脚本（Linux / macOS）
#
# 定位：这是一个"引导型"脚本，方便新人 clone AgentCore 后快速把本地
#       OpenSandbox 服务器跑起来。它完全委派上游官方链路，**不**在本仓库
#       内嵌 server 配置 / 镜像 tag，**不**托管进程生命周期（前台运行，
#       Ctrl+C 即停）。OpenSandbox server 仍是独立的第三方服务。
#
# 步骤：
#   1. 校验 Docker 就绪
#   2. 确保有 uv / uvx
#   3. 仅当 ~/.sandbox.toml 不存在时，调官方 init-config 生成配置
#   4. 前台启动 uvx opensandbox-server，打印下一步提示
#
# 用法:
#   ./scripts/opensandbox-quickstart.sh
#   PORT=8080 ./scripts/opensandbox-quickstart.sh
# =============================================================================
set -euo pipefail

PORT="${PORT:-8080}"

step() { printf '\033[36m[opensandbox] %s\033[0m\n' "$*"; }
err()  { printf '\033[31m[opensandbox] %s\033[0m\n' "$*" >&2; }

# -----------------------------------------------------------------------------
# 1. Docker 就绪检查
# -----------------------------------------------------------------------------
step "检查 Docker 是否就绪 ..."
if ! command -v docker >/dev/null 2>&1; then
    err "未找到 docker 命令。请先安装 Docker 并加入 PATH。"
    exit 1
fi
if ! docker version >/dev/null 2>&1; then
    err "Docker 守护进程未运行。请启动 Docker 服务后重试。"
    exit 1
fi
step "Docker 就绪。"

# -----------------------------------------------------------------------------
# 2. uv / uvx 检查
# -----------------------------------------------------------------------------
if ! command -v uvx >/dev/null 2>&1 && ! command -v uv >/dev/null 2>&1; then
    err "未找到 uv / uvx。请安装：curl -LsSf https://astral.sh/uv/install.sh | sh  或  pip install uv"
    exit 1
fi
step "uv 就绪。"

# -----------------------------------------------------------------------------
# 3. 生成配置（仅当不存在时，交给官方 init-config，不内嵌到本仓库）
# -----------------------------------------------------------------------------
CONFIG_PATH="${HOME}/.sandbox.toml"
if [ ! -f "$CONFIG_PATH" ]; then
    step "未检测到 $CONFIG_PATH，调用官方 init-config 生成 Docker 运行时配置 ..."
    uvx opensandbox-server init-config "$CONFIG_PATH" --example docker
else
    step "已存在 $CONFIG_PATH，跳过 init-config（如需重置请手动删除）。"
fi

# -----------------------------------------------------------------------------
# 3b. AIO Sandbox 强制需要 seccomp=unconfined，自动确保已写入
#     等价于 docker run --security-opt seccomp=unconfined，否则容器内 Chromium /
#     Playwright / VNC 会因 Docker 默认 seccomp 封锁 clone3/unshare 而启动失败。
#     详见 https://github.com/agent-infra/sandbox README “Installation“ 一节。
# -----------------------------------------------------------------------------
if grep -Eq '^seccomp_profile[[:space:]]*=[[:space:]]*""' "$CONFIG_PATH"; then
    step "将 [docker].seccomp_profile 从 '' 自动改写为 'unconfined'（AIO Sandbox 需要）..."
    # macOS 与 Linux 的 sed -i 行为不一致，写到临时文件后 mv。
    tmp="${CONFIG_PATH}.tmp.$$"
    sed -E 's/^(seccomp_profile[[:space:]]*=[[:space:]]*)"".*/\1"unconfined"/' "$CONFIG_PATH" > "$tmp"
    mv "$tmp" "$CONFIG_PATH"
elif ! grep -Eq '^seccomp_profile[[:space:]]*=[[:space:]]*"unconfined"' "$CONFIG_PATH"; then
    step "[docker] 节未含 seccomp_profile，追加 seccomp_profile = \"unconfined\" ..."
    if grep -Eq '^\[docker\]' "$CONFIG_PATH"; then
        awk '/^\[docker\]/{print; print "seccomp_profile = \"unconfined\""; next} {print}' "$CONFIG_PATH" > "${CONFIG_PATH}.tmp.$$" && mv "${CONFIG_PATH}.tmp.$$" "$CONFIG_PATH"
    else
        printf '\n[docker]\nseccomp_profile = "unconfined"\n' >> "$CONFIG_PATH"
    fi
else
    step "[docker].seccomp_profile 已为 'unconfined'，无需修改。"
fi

# -----------------------------------------------------------------------------
# 4. 前台启动（Ctrl+C 即停，不做 PID / stop 进程托管）
# -----------------------------------------------------------------------------
step "前台启动 OpenSandbox 服务器（按 Ctrl+C 停止）..."
echo
echo "  默认监听: http://localhost:${PORT}"
echo "  下一步: 打开 AgentCore -> 设置 -> 沙箱控制平面，填写 URL 并点击'启用连接'。"
echo

exec uvx opensandbox-server
