# =============================================================================
# OpenSandbox 服务器本地快速启动脚本（Windows PowerShell）
#
# 定位：这是一个"引导型"脚本，方便新人 clone AgentCore 后快速把本地
#       OpenSandbox 服务器跑起来。它完全委派上游官方链路，**不**在本仓库
#       内嵌 server 配置 / 镜像 tag，**不**托管进程生命周期（前台运行，
#       Ctrl+C 即停）。OpenSandbox server 仍是独立的第三方服务。
#
# 步骤：
#   1. 校验 Docker 就绪（未就绪则报错并提示需 WSL2）
#   2. 确保有 uv / uvx（无则提示安装）
#   3. 仅当 ~/.sandbox.toml 不存在时，调官方 `init-config` 生成配置
#   4. 前台启动 `uvx opensandbox-server`，打印下一步提示
#
# 用法:
#   .\scripts\opensandbox-quickstart.ps1
#   .\scripts\opensandbox-quickstart.ps1 -Port 8080
# =============================================================================
param(
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "[opensandbox] $Message" -ForegroundColor Cyan
}

function Write-Err([string]$Message) {
    Write-Host "[opensandbox] $Message" -ForegroundColor Red
}

# -----------------------------------------------------------------------------
# 1. Docker 就绪检查
# -----------------------------------------------------------------------------
Write-Step "检查 Docker 是否就绪 ..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "未找到 docker 命令。请先安装 Docker Desktop（Windows 需启用 WSL2）并加入 PATH。"
    exit 1
}
try {
    docker version --format "{{.Server.Version}}" | Out-Null
} catch {
    Write-Err "Docker 守护进程未运行。请启动 Docker Desktop（Windows 需 WSL2 后端）后重试。"
    exit 1
}
Write-Step "Docker 就绪。"

# -----------------------------------------------------------------------------
# 2. uv / uvx 检查
#    优先 PATH 查找（快）；失败后自动到常见 pip-user 安装目录兵底，
#    命中则把目录临时注入当前会话 $env:PATH（不修改系统注册表）。
# -----------------------------------------------------------------------------
function Resolve-UvCommand([string]$Name) {
    $hit = Get-Command $Name -ErrorAction SilentlyContinue
    if ($hit) { return $hit.Source }
    $candidates = @()
    # pip --user 安装目录：AppData\Roaming\Python\PythonXY\Scripts
    $userPy = Join-Path $env:APPDATA 'Python'
    if (Test-Path $userPy) {
        $candidates += Get-ChildItem -Path $userPy -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName 'Scripts' }
    }
    # pipx / uv 官方安装器默认目录
    $candidates += (Join-Path $env:USERPROFILE '.local\bin')
    # Python 官方安装器 user scope
    if ($env:LOCALAPPDATA) {
        $pyLocal = Join-Path $env:LOCALAPPDATA 'Programs\Python'
        if (Test-Path $pyLocal) {
            $candidates += Get-ChildItem -Path $pyLocal -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName 'Scripts' }
        }
    }
    foreach ($dir in $candidates) {
        if (-not (Test-Path $dir)) { continue }
        $exe = Join-Path $dir "$Name.exe"
        if (Test-Path $exe) { return $exe }
    }
    return $null
}

$uvxPath = Resolve-UvCommand 'uvx'
$uvPath  = Resolve-UvCommand 'uv'
if (-not $uvxPath -and -not $uvPath) {
    Write-Err "未找到 uv / uvx。请安装：pip install uv（若已安装，请确认其 Scripts 目录在 PATH 里，参见脚本头部注释）（或访问 https://docs.astral.sh/uv/ ）"
    exit 1
}
# 如果命中不在 PATH，将目录临时加入本会话 PATH，后面直接uvx/uv 命令才能调到
foreach ($found in @($uvxPath, $uvPath)) {
    if ($found) {
        $dir = Split-Path -Parent $found
        if (($env:Path -split ';') -notcontains $dir) {
            $env:Path = "$dir;$env:Path"
            Write-Step "已临时将 $dir 加入本会话 PATH（uv 自动定位命中）。"
        }
    }
}
$uvDisplay = if ($uvxPath) { $uvxPath } else { $uvPath }
Write-Step "uv 就绪：$uvDisplay"

# -----------------------------------------------------------------------------
# 3. 生成配置（仅当不存在时，交给官方 init-config，不内嵌到本仓库）
# -----------------------------------------------------------------------------
$ConfigPath = Join-Path $HOME ".sandbox.toml"
if (-not (Test-Path $ConfigPath)) {
    Write-Step "未检测到 $ConfigPath，调用官方 init-config 生成 Docker 运行时配置 ..."
    uvx opensandbox-server init-config $ConfigPath --example docker
    if ($LASTEXITCODE -ne 0) {
        Write-Err "init-config 失败（退出码 $LASTEXITCODE）。"
        exit $LASTEXITCODE
    }
} else {
    Write-Step "已存在 $ConfigPath，跳过 init-config（如需重置请手动删除）。"
}

# -----------------------------------------------------------------------------
# 3b. AIO Sandbox 强制需要 seccomp=unconfined，自动确保已写入
#     等价于 docker run --security-opt seccomp=unconfined，否则容器内 Chromium /
#     Playwright / VNC 会因 Docker 默认 seccomp 封锁 clone3/unshare 而启动失败。
#     详见 https://github.com/agent-infra/sandbox README “Installation“ 一节。
# -----------------------------------------------------------------------------
$configText = Get-Content -Path $ConfigPath -Raw
if ($configText -match '(?m)^seccomp_profile\s*=\s*""') {
    Write-Step "将 [docker].seccomp_profile 从 ‘‘ 自动改写为 ‘unconfined’（AIO Sandbox 需要）..."
    $patched = $configText -replace '(?m)^(seccomp_profile\s*=\s*)""', '$1"unconfined"'
    Set-Content -Path $ConfigPath -Value $patched -Encoding UTF8 -NoNewline
} elseif ($configText -notmatch '(?m)^seccomp_profile\s*=\s*"unconfined"') {
    Write-Step "[docker] 节未含 seccomp_profile，追加 seccomp_profile = "unconfined" ..."
    $appended = if ($configText -match '(?m)\[docker\]') {
        $configText -replace '(?m)^(\[docker\][^\r\n]*)', "`$1`nseccomp_profile = `"unconfined`""
    } else {
        $configText + "`n[docker]`nseccomp_profile = `"unconfined`"`n"
    }
    Set-Content -Path $ConfigPath -Value $appended -Encoding UTF8 -NoNewline
} else {
    Write-Step "[docker].seccomp_profile 已为 ‘unconfined’，无需修改。"
}

# -----------------------------------------------------------------------------
# 4. 前台启动（Ctrl+C 即停，不做 PID / stop 进程托管）
# -----------------------------------------------------------------------------
Write-Step "前台启动 OpenSandbox 服务器（按 Ctrl+C 停止）..."
Write-Host ""
Write-Host "  默认监听: http://localhost:$Port" -ForegroundColor Green
Write-Host "  下一步: 打开 AgentCore -> 设置 -> 沙箱控制平面，填写 URL 并点击'启用连接'。" -ForegroundColor Green
Write-Host ""

uvx opensandbox-server
exit $LASTEXITCODE
