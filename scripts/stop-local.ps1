# =============================================================================
# AgentCore 一键停止脚本（Windows PowerShell）
#
#   1. 按端口停止后端 uvicorn（默认 8000）
#   2. 按端口停止前端 vite dev server（默认 3000）
#   3. 清理 Windows 下 uvicorn --reload 崩溃后残留的“孤儿 worker”进程
#
# 背景：uvicorn --reload 在 Windows 上通过 multiprocessing.spawn 派生子 worker。
# 直接杀掉父进程（reloader）时，子 worker 会变成孤儿继续存活并占用端口、以旧
# 代码响应请求 —— 表现为“改了代码 / 删了 agent 重启后仍不生效”。本脚本用
# taskkill /T 终止整个进程树，并额外扫描、清理这类孤儿 worker。
#
# 用法:
#   .\scripts\stop-local.ps1                    # 停止后端 + 前端 + 清理孤儿
#   .\scripts\stop-local.ps1 -BackendOnly       # 只停后端
#   .\scripts\stop-local.ps1 -FrontendOnly       # 只停前端
#   .\scripts\stop-local.ps1 -BackendPort 8001   # 指定后端端口
#   .\scripts\stop-local.ps1 -SkipPurgeOrphans   # 跳过孤儿 worker 清理
# =============================================================================
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [switch]$SkipPurgeOrphans
)

$ErrorActionPreference = "Continue"

function Write-Step([string]$Message) { Write-Host "[agentcore] $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "[agentcore] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[agentcore] $Message" -ForegroundColor Yellow }

# 终止监听某端口的进程及其整个子进程树。
# Windows 上多个 uvicorn 进程可能共享同一 LISTEN socket（SO_REUSEADDR），
# Get-NetTCPConnection 只报告其中一个 PID（且可能是已死 reloader 的 PID，
# socket 被子进程继承），故用 taskkill /T 连带清除 worker，并在结尾校验。
function Test-PidAlive([int]$Id) {
    return $null -ne (Get-Process -Id $Id -ErrorAction SilentlyContinue)
}

function Kill-ByPort {
    param([int]$Port, [string]$Label)

    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Step "$Label 端口 $Port 无监听进程，跳过"
        return
    }

    foreach ($procId in ($conns.OwningProcess | Sort-Object -Unique)) {
        if (-not (Test-PidAlive $procId)) {
            Write-Step "$Label 端口 $Port 的报告 PID $procId 已退出（socket 被子进程继承），转由孤儿清理/兜底处理"
            continue
        }
        $null = & taskkill /PID $procId /T /F 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "已停止 $Label (PID $procId 及其子进程树)"
        } else {
            # 权限不足时回退到 Stop-Process
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Ok "已停止 $Label (PID $procId)"
            } catch {
                Write-Warn "停止 $Label PID $procId 失败：$($_.Exception.Message)"
                Write-Warn "如反复失败，请在管理员终端执行：taskkill /PID $procId /T /F"
            }
        }
    }
}

# 兜底：清理所有命令行含 “uvicorn agentcore” 的存活进程（reloader / 单进程本体），
# 覆盖 Get-NetTCPConnection 未报告、但仍在跑的 uvicorn 父进程。
function Purge-UvicornProcesses {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'uvicorn\s+agentcore' }
    if (-not $procs) { return }
    foreach ($p in $procs) {
        $null = & taskkill /PID $p.ProcessId /T /F 2>&1
        Write-Ok "兜底清理 uvicorn 进程 PID $($p.ProcessId)"
    }
}

# 清理“孤儿 worker”：命令行含 multiprocessing.spawn、且父进程已不存在的 python。
# 这些是 uvicorn --reload 父进程崩溃/被杀后残留、仍可能占用端口的旧代码进程。
function Purge-OrphanWorkers {
    $all = Get-CimInstance Win32_Process
    $existingPids = @{}
    foreach ($p in $all) { $existingPids[[int]$p.ProcessId] = $true }

    $spawns = $all | Where-Object {
        $_.Name -eq 'python.exe' -and $_.CommandLine -match 'multiprocessing\.spawn'
    }

    $orphans = $spawns | Where-Object { -not $existingPids.ContainsKey([int]$_.ParentProcessId) }
    if (-not $orphans) {
        Write-Step "未发现孤儿 worker 进程"
        return
    }

    foreach ($o in $orphans) {
        $null = & taskkill /PID $o.ProcessId /T /F 2>&1
        Write-Ok "清理孤儿 worker PID $($o.ProcessId)（父进程 $($o.ParentProcessId) 已退出）"
    }
}

Write-Step "开始停止 AgentCore 服务 ..."

if (-not $FrontendOnly) {
    Kill-ByPort -Port $BackendPort -Label "后端"
}
if (-not $BackendOnly) {
    Kill-ByPort -Port $FrontendPort -Label "前端"
}

if (-not $SkipPurgeOrphans) {
    Purge-OrphanWorkers
}

# 后端兜底：清理 Get-NetTCPConnection 未报告、但仍在跑的 uvicorn 父进程
if (-not $FrontendOnly) {
    Purge-UvicornProcesses
}

# 最终校验：确认端口已真正释放（处理死 reloader + 活 worker 继承 socket 的情况）
foreach ($check in @(@{Port=$BackendPort; Label='后端'}, @{Port=$FrontendPort; Label='前端'})) {
    if ($check.Port -eq 0) { continue }
    if ($BackendOnly -and $check.Label -eq '前端') { continue }
    if ($FrontendOnly -and $check.Label -eq '后端') { continue }
    $still = Get-NetTCPConnection -LocalPort $check.Port -State Listen -ErrorAction SilentlyContinue
    if ($still) {
        # 端口仍占用 → 再抢一次当前存活 holder 的进程树
        foreach ($procId in ($still.OwningProcess | Sort-Object -Unique)) {
            if (Test-PidAlive $procId) {
                $null = & taskkill /PID $procId /T /F 2>&1
                Write-Warn "$($check.Label) 端口 $check.Port 仍占用，已补杀 PID $procId"
            }
        }
        $still2 = Get-NetTCPConnection -LocalPort $check.Port -State Listen -ErrorAction SilentlyContinue
        if ($still2) {
            Write-Warn "$($check.Label) 端口 $check.Port 仍被占用，可能需管理员权限：taskkill /PID $(($still2.OwningProcess|Sort-Object -Unique) -join ',') /T /F"
        } else {
            Write-Ok "$($check.Label) 端口 $check.Port 已释放 ✅"
        }
    }
}

Write-Ok "停止完成。"
