# =============================================================================
# AgentCore 一键启动脚本（Windows PowerShell）
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
#   .\scripts\start-local.ps1                # 一键启动
#   .\scripts\start-local.ps1 -NoBrowser     # 不自动打开浏览器
#   .\scripts\start-local.ps1 -InstallDevDeps# 安装含 pytest 的开发依赖
# =============================================================================
param(
    [string]$HostName = "127.0.0.1",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$InstallDevDeps,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host "[agentcore] $Message" -ForegroundColor Cyan
}

# -----------------------------------------------------------------------------
# 1. Python 环境检查
# -----------------------------------------------------------------------------
function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py -3" }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    throw "未找到 Python。请安装 Python 3.11+ 并加入 PATH。"
}

$PythonCmd = Get-PythonCommand
$VenvPath = Join-Path $Root ".venv"
if (-not (Test-Path $VenvPath)) {
    Write-Step "创建虚拟环境 .venv ..."
    Invoke-Expression "$PythonCmd -m venv .venv"
}

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "虚拟环境 Python 不存在: $VenvPython"
}
Write-Step "Python 环境就绪: $VenvPython"

# -----------------------------------------------------------------------------
# 2. deepagents 本地路径校验（支持环境变量 DEEPAGENTS_PATH 覆盖）
# -----------------------------------------------------------------------------
$DeepAgentsPath = if ($env:DEEPAGENTS_PATH) { $env:DEEPAGENTS_PATH } else { "D:\code\deepagents\libs\deepagents" }
if (-not (Test-Path $DeepAgentsPath)) {
    throw "deepagents 本地路径不存在: $DeepAgentsPath —— 请先克隆/检出 deepagents 仓库，或设置环境变量 DEEPAGENTS_PATH。"
}
Write-Step "deepagents 路径校验通过: $DeepAgentsPath"

# -----------------------------------------------------------------------------
# 3. 安装后端依赖
# -----------------------------------------------------------------------------
Write-Step "安装 deepagents 本地依赖: $DeepAgentsPath ..."
& $VenvPython -m pip install -q -e $DeepAgentsPath
if ($LASTEXITCODE -ne 0) { throw "deepagents 安装失败" }

Write-Step "安装后端依赖 (pip install -e .) ..."
if ($InstallDevDeps) {
    & $VenvPython -m pip install -q -e ".[dev]"
} else {
    & $VenvPython -m pip install -q -e .
}
if ($LASTEXITCODE -ne 0) { throw "后端依赖安装失败" }

# -----------------------------------------------------------------------------
# 4. 前端依赖检查 + 模式判定
#    console/dist 存在 → 生产模式：后端直接托管静态文件，不启动 dev server
# -----------------------------------------------------------------------------
$ConsoleDir = Join-Path $Root "console"
$ConsoleDist = Join-Path $ConsoleDir "dist"
$ProductionMode = Test-Path (Join-Path $ConsoleDist "index.html")

if ($ProductionMode) {
    Write-Step "检测到 console/dist —— 生产模式：由后端托管前端静态文件"
} else {
    Write-Step "开发模式：console/dist 不存在，将并行启动 vite dev server"
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "未找到 npm。开发模式需要 Node.js，请先安装，或先执行 npm run build 生成 console/dist。"
    }
    $NodeModules = Join-Path $ConsoleDir "node_modules"
    if (-not (Test-Path $NodeModules)) {
        Write-Step "console/node_modules 不存在，执行 npm install ..."
        Push-Location $ConsoleDir
        try {
            & npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install 失败" }
        } finally {
            Pop-Location
        }
    } else {
        Write-Step "前端依赖已就绪 (console/node_modules)"
    }
}

# -----------------------------------------------------------------------------
# 5. 并行启动后端 + 前端（后台进程，日志写入 logs/）
# -----------------------------------------------------------------------------
$LogDir = Join-Path $Root "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

Write-Step "启动后端 uvicorn (--workers 1) ..."
$BackendProc = Start-Process -FilePath $VenvPython `
    -ArgumentList "-m", "uvicorn", "agentcore.api:app", "--host", $HostName, "--port", "$BackendPort", "--workers", "1" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput (Join-Path $LogDir "backend.out.log") `
    -RedirectStandardError (Join-Path $LogDir "backend.err.log") `
    -PassThru -WindowStyle Hidden

$FrontendProc = $null
if (-not $ProductionMode) {
    Write-Step "启动前端 vite dev server ..."
    $FrontendProc = Start-Process -FilePath "npm.cmd" `
        -ArgumentList "run", "dev", "--", "--port", "$FrontendPort", "--strictPort" `
        -WorkingDirectory $ConsoleDir `
        -RedirectStandardOutput (Join-Path $LogDir "frontend.out.log") `
        -RedirectStandardError (Join-Path $LogDir "frontend.err.log") `
        -PassThru -WindowStyle Hidden
}

# -----------------------------------------------------------------------------
# 6. 轮询 /health 等待后端就绪（最长 90 秒）
# -----------------------------------------------------------------------------
$HealthUrl = "http://$HostName`:$BackendPort/health"
Write-Step "等待后端就绪: $HealthUrl"
$Ready = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    if ($BackendProc.HasExited) {
        Get-Content (Join-Path $LogDir "backend.err.log") -Tail 30 -ErrorAction SilentlyContinue |
            Write-Host -ForegroundColor Red
        throw "后端进程已退出（exit code: $($BackendProc.ExitCode)），请查看 logs/backend.err.log"
    }
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $Ready = $true; break }
    } catch {
        # 尚未就绪，继续轮询
    }
}
if (-not $Ready) {
    throw "后端在 90 秒内未就绪，请查看 logs/backend.err.log"
}
Write-Step "后端已就绪 ✅"

# -----------------------------------------------------------------------------
# 7. 打开浏览器并打印入口
# -----------------------------------------------------------------------------
if ($ProductionMode) {
    $EntryUrl = "http://$HostName`:$BackendPort/"
} else {
    $EntryUrl = "http://localhost`:$FrontendPort/"
}

if (-not $NoBrowser) {
    Write-Step "打开浏览器: $EntryUrl"
    Start-Process $EntryUrl
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  AgentCore 已启动" -ForegroundColor Green
Write-Host "  前端入口   : $EntryUrl" -ForegroundColor Green
Write-Host "  后端 API   : http://$HostName`:$BackendPort  (/health, /docs)" -ForegroundColor Green
if ($ProductionMode) {
    Write-Host "  托管模式   : 生产模式（后端托管 console/dist）" -ForegroundColor Green
} else {
    Write-Host "  托管模式   : 开发模式（vite dev + API 代理）" -ForegroundColor Green
}
Write-Host "  日志目录   : $LogDir" -ForegroundColor Green
Write-Host "  停止服务   : Stop-Process -Id $($BackendProc.Id)$(if ($FrontendProc) { ", $($FrontendProc.Id)" })" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
