# AgentCore Installer for Windows (self-contained: includes uv download via GitHub)
# Usage: irm <url>/install.ps1 | iex
#    or: .\install.ps1 [-Version X.Y.Z] [-FromSource] [-SourceDir DIR]
#                            [-Extras "dev"] [-UvPath PATH]
#
# Installs AgentCore into ~/.agentcore with a uv-managed Python environment.
# Users do NOT need Python pre-installed — uv handles everything.
#
# uv is obtained automatically:
#   1. Already on PATH or in common locations
#   2. Downloaded via https://astral.sh/uv/install.ps1
#   3. Downloaded via GitHub Releases if astral.sh is unreachable (e.g. in China)

& {
param(
    [string]$SourceDir = "",
    [string]$Extras    = "",
    [string]$UvPath    = "",
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ── Defaults ──────────────────────────────────────────────────────────────────
$AgentcoreHome   = if ($env:AGENTCORE_HOME) { $env:AGENTCORE_HOME } else { Join-Path $HOME ".agentcore" }
$AgentcoreVenv   = Join-Path $AgentcoreHome "venv"
$AgentcoreBin    = Join-Path $AgentcoreHome "bin"
$PythonVersion   = "3.12"
$AgentcoreRepo   = "https://github.com/your-org/AgentCore.git"

# ── Colors ────────────────────────────────────────────────────────────────────
function Write-Info  { param([string]$Message) Write-Host "[agentcore] " -ForegroundColor Green  -NoNewline; Write-Host $Message }
function Write-Warn  { param([string]$Message) Write-Host "[agentcore] " -ForegroundColor Yellow -NoNewline; Write-Host $Message }
function Write-Err   { param([string]$Message) Write-Host "[agentcore] " -ForegroundColor Red    -NoNewline; Write-Host $Message }
function Stop-WithError { param([string]$Message) Write-Err $Message; exit 1 }

# ── Help ──────────────────────────────────────────────────────────────────────
if ($Help) {
    @"
AgentCore Installer for Windows

Usage: .\install.ps1 [OPTIONS]

Options:
  -SourceDir <DIR>      Install from local source directory
                        (default: auto-clone from GitHub)
  -Extras <EXTRAS>      Comma-separated optional extras (e.g. dev)
  -UvPath <PATH>        Path to a pre-installed uv.exe (skips all auto-install)
  -Help                 Show this help

Environment:
  AGENTCORE_HOME       Installation directory (default: ~/.agentcore)
"@
    exit 0
}

Write-Host "[agentcore] " -ForegroundColor Green -NoNewline
Write-Host "Installing AgentCore into " -NoNewline
Write-Host "$AgentcoreHome" -ForegroundColor White

# ── Execution Policy Check ────────────────────────────────────────────────────
$policy = Get-ExecutionPolicy
if ($policy -eq "Restricted") {
    Write-Info "Execution policy is 'Restricted', setting to RemoteSigned for current user..."
    try {
        Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
        Write-Info "Execution policy updated to RemoteSigned"
    } catch {
        Write-Err "PowerShell execution policy prevents script execution."
        Write-Err "Please run: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser"
        exit 1
    }
}

# ── Step 1: Ensure uv ────────────────────────────────────────────────────────

function Invoke-UvFromGitHub {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
    $url  = "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip"
    $dest = Join-Path $env:LOCALAPPDATA "uv"
    $zip  = Join-Path $env:TEMP "uv-gh-$([System.IO.Path]::GetRandomFileName()).zip"

    Write-Info "Downloading uv ($arch) from GitHub Releases..."
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    } catch {
        throw "GitHub download failed: $_"
    }

    if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }
    Write-Info "Extracting uv..."
    try {
        Expand-Archive -Force -Path $zip -DestinationPath $dest
    } catch {
        Remove-Item $zip -ErrorAction SilentlyContinue
        throw "Extraction failed: $_"
    }
    Remove-Item $zip -ErrorAction SilentlyContinue
    $uvExe = Join-Path $dest "uv.exe"
    if (-not (Test-Path $uvExe)) { throw "uv.exe not found after extraction at $dest" }
    $env:PATH = "$dest;$env:PATH"
    Write-Info "uv installed from GitHub: $uvExe"
}

function Ensure-Uv {
    # 0. User-supplied path
    if ($UvPath) {
        if (-not (Test-Path $UvPath)) { Stop-WithError "Specified uv not found: $UvPath" }
        $env:PATH = "$(Split-Path $UvPath -Parent);$env:PATH"
        Write-Info "uv found: $UvPath"; return
    }
    # 1. Already on PATH
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Info "uv found: $((Get-Command uv).Source)"; return
    }
    # 2. Common install locations
    $candidates = @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $HOME ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $env:PATH = "$(Split-Path $candidate -Parent);$env:PATH"
            Write-Info "uv found: $candidate"; return
        }
    }
    # 3. Try astral.sh
    Write-Info "Installing uv via astral.sh..."
    $astralOk = $false
    try {
        $installScript = Invoke-RestMethod https://astral.sh/uv/install.ps1 -TimeoutSec 15
        Invoke-Expression $installScript
        $astralOk = $true
    } catch {
        Write-Warn "astral.sh unreachable, falling back to GitHub Releases..."
    }
    if ($astralOk) {
        $uvPaths = @(
            (Join-Path $HOME ".local\bin"),
            (Join-Path $HOME ".cargo\bin"),
            (Join-Path $env:LOCALAPPDATA "uv")
        )
        foreach ($p in $uvPaths) {
            if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) { $env:PATH = "$p;$env:PATH" }
        }
        if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Info "uv installed via astral.sh"; return }
        Write-Warn "astral.sh install succeeded but uv not found, trying GitHub Releases..."
    }
    # 4. GitHub Releases fallback
    try {
        Invoke-UvFromGitHub
    } catch {
        Stop-WithError "Failed to install uv automatically: $_`nPlease install manually: https://docs.astral.sh/uv/"
    }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Stop-WithError "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
    }
}

Ensure-Uv

# ── Step 2: Create / update virtual environment ──────────────────────────────
if (Test-Path $AgentcoreVenv) {
    Write-Info "Existing environment found, upgrading..."
} else {
    Write-Info "Creating Python $PythonVersion environment..."
}

uv venv $AgentcoreVenv --python $PythonVersion --quiet --clear
if ($LASTEXITCODE -ne 0) { Stop-WithError "Failed to create virtual environment" }

$VenvPython = Join-Path $AgentcoreVenv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { Stop-WithError "Failed to create virtual environment" }

$pyVersion = & $VenvPython --version 2>&1
Write-Info "Python environment ready ($pyVersion)"

# ── Step 3: Install AgentCore ─────────────────────────────────────────────────
$ExtrasSuffix = ""
if ($Extras) { $ExtrasSuffix = "[$Extras]" }

$script:ConsoleCopied   = $false
$script:ConsoleAvailable = $false

function Prepare-Console {
    param([string]$RepoDir)
    $consoleSrc  = Join-Path $RepoDir "console\dist"
    # api.py resolves _CONSOLE_DIST = Path(__file__).parents[2] / "console" / "dist"
    # For venv install, parents[2] is the venv root
    $consoleDest = Join-Path $AgentcoreVenv "console\dist"

    if (Test-Path (Join-Path $consoleDest "index.html")) { $script:ConsoleAvailable = $true; return }
    if ((Test-Path $consoleSrc) -and (Test-Path (Join-Path $consoleSrc "index.html"))) {
        Write-Info "Copying console frontend assets..."
        New-Item -ItemType Directory -Path $consoleDest -Force | Out-Null
        Copy-Item -Path "$consoleSrc\*" -Destination $consoleDest -Recurse -Force
        $script:ConsoleCopied = $true; $script:ConsoleAvailable = $true; return
    }
    $packageJson = Join-Path $RepoDir "console\package.json"
    if (-not (Test-Path $packageJson)) {
        Write-Warn "Console source not found - the web UI won't be available."; return
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Warn "npm not found - skipping console frontend build."
        Write-Warn "Install Node.js from https://nodejs.org/ then re-run."; return
    }
    Write-Info "Building console frontend (npm ci && npm run build)..."
    Push-Location (Join-Path $RepoDir "console")
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { Write-Warn "npm ci failed - the web UI won't be available."; return }
        npm run build
        if ($LASTEXITCODE -ne 0) { Write-Warn "npm run build failed - the web UI won't be available."; return }
    } finally { Pop-Location }
    if (Test-Path (Join-Path $consoleSrc "index.html")) {
        New-Item -ItemType Directory -Path $consoleDest -Force | Out-Null
        Copy-Item -Path "$consoleSrc\*" -Destination $consoleDest -Recurse -Force
        $script:ConsoleCopied = $true; $script:ConsoleAvailable = $true
        Write-Info "Console frontend built successfully"; return
    }
    Write-Warn "Console build completed but index.html not found."
}

function Cleanup-Console {
    if ($script:ConsoleCopied) {
        $consoleDest = Join-Path $AgentcoreVenv "console"
        if (Test-Path $consoleDest) { Remove-Item -Path "$consoleDest\*" -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

$VenvAgentcore = Join-Path $AgentcoreVenv "Scripts\agentcore-api.exe"

# AgentCore is not yet on PyPI, so we always install from source.
# Either from a local directory (-SourceDir DIR) or by cloning from GitHub.
if ($SourceDir) {
    # User provided local source path
    $SourceDir = (Resolve-Path $SourceDir).Path
    Write-Info "Installing AgentCore from local source: $SourceDir"
    Prepare-Console $SourceDir
    Write-Info "Installing package from source..."
    uv pip install "${SourceDir}${ExtrasSuffix}" --python $VenvPython
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Installation from source failed" }
    Cleanup-Console
} else {
    # Clone from GitHub
    Write-Info "Cloning AgentCore from GitHub..."
    $cloneDir = Join-Path $AgentcoreHome "src"
    if (Test-Path (Join-Path $cloneDir ".git")) {
        Write-Info "Existing source found, pulling latest changes..."
        Push-Location $cloneDir
        try {
            git pull --quiet 2>$null
            if ($LASTEXITCODE -ne 0) { Write-Warn "git pull failed, using existing source" }
        } finally { Pop-Location }
    } else {
        git clone --depth 1 $AgentcoreRepo $cloneDir
        if ($LASTEXITCODE -ne 0) { Stop-WithError "Failed to clone repository" }
    }
    Prepare-Console $cloneDir
    Write-Info "Installing package from source..."
    uv pip install "${cloneDir}${ExtrasSuffix}" --python $VenvPython
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Installation from source failed" }
    Cleanup-Console
}

# Verify CLI entry point
if (-not (Test-Path $VenvAgentcore)) { Stop-WithError "Installation failed: agentcore-api not found in venv" }
Write-Info "AgentCore installed successfully"

# ── Step 4: Create wrapper scripts ───────────────────────────────────────────
New-Item -ItemType Directory -Path $AgentcoreBin -Force | Out-Null

# PowerShell wrapper
$wrapperPs1 = Join-Path $AgentcoreBin "agentcore-api.ps1"
$wrapperPs1Content = @'
# AgentCore CLI wrapper — delegates to the uv-managed environment.
$ErrorActionPreference = "Stop"
$AgentcoreHome = if ($env:AGENTCORE_HOME) { $env:AGENTCORE_HOME } else { Join-Path $HOME ".agentcore" }
$RealBin = Join-Path $AgentcoreHome "venv\Scripts\agentcore-api.exe"
if (-not (Test-Path $RealBin)) {
    Write-Error "AgentCore environment not found at $AgentcoreHome\venv"
    Write-Error "Please reinstall: irm <install-url> | iex"
    exit 1
}
& $RealBin @args
'@
Set-Content -Path $wrapperPs1 -Value $wrapperPs1Content -Encoding UTF8
Write-Info "PowerShell wrapper created at $wrapperPs1"

# CMD wrapper
$wrapperCmd = Join-Path $AgentcoreBin "agentcore-api.cmd"
$wrapperCmdContent = @"
@echo off
REM AgentCore CLI wrapper — delegates to the uv-managed environment.
set "AGENTCORE_HOME=%AGENTCORE_HOME%"
if "%AGENTCORE_HOME%"=="" set "AGENTCORE_HOME=%USERPROFILE%\.agentcore"
set "REAL_BIN=%AGENTCORE_HOME%\venv\Scripts\agentcore-api.exe"
if not exist "%REAL_BIN%" (
    echo Error: AgentCore environment not found at %AGENTCORE_HOME%\venv >&2
    echo Please reinstall >&2
    exit /b 1
)
"%REAL_BIN%" %*
"@
Set-Content -Path $wrapperCmd -Value $wrapperCmdContent -Encoding UTF8
Write-Info "CMD wrapper created at $wrapperCmd"

# ── Step 5: Update PATH via User Environment Variable ────────────────────────
$targetPath = $AgentcoreBin
$registryPath = "HKCU:\Environment"

try {
    $currentUserPath = (Get-ItemProperty -Path $registryPath -Name Path -ErrorAction SilentlyContinue).Path
    if (-not $currentUserPath) { $currentUserPath = "" }
} catch { $currentUserPath = "" }

$pathArray = $currentUserPath -split ';' | ForEach-Object { $_.Trim() }
$isAlreadyAdded = $pathArray -contains $targetPath

if (-not $isAlreadyAdded) {
    if ($currentUserPath) { $newUserPath = "$targetPath;$currentUserPath" }
    else { $newUserPath = $targetPath }

    try {
        if (-not (Test-Path $registryPath)) { New-Item -Path $registryPath -Force | Out-Null }
        Set-ItemProperty -Path $registryPath -Name Path -Value $newUserPath
        $env:Path = "$targetPath;$env:Path"
        Write-Info "Added $targetPath to User PATH"
    } catch {
        Write-Host ""
        Write-Host "[CRITICAL WARNING] Automatic PATH update failed." -ForegroundColor Red
        Write-Host "   Please manually add to User PATH: $targetPath" -ForegroundColor Red
        Write-Host ""
        try { $env:Path = "$targetPath;$env:Path" } catch {}
    }
} else {
    Write-Info "$targetPath is already in User PATH"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "AgentCore installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "  Install location:  " -NoNewline; Write-Host "$AgentcoreHome" -ForegroundColor White
Write-Host "  Python:            " -NoNewline; Write-Host "$pyVersion" -ForegroundColor White
if ($script:ConsoleAvailable) {
    Write-Host "  Console (web UI):  " -NoNewline; Write-Host "available" -ForegroundColor Green
} else {
    Write-Host "  Console (web UI):  " -NoNewline; Write-Host "not available" -ForegroundColor Yellow
    Write-Host "                     Install Node.js and re-run to enable the web UI."
}
Write-Host ""
Write-Host "To get started, open a new terminal and run:"
Write-Host ""
Write-Host "  agentcore-api" -ForegroundColor White -NoNewline
Write-Host "        # start AgentCore server"
Write-Host ""
Write-Host "  Default: http://127.0.0.1:8000/"
Write-Host ""
Write-Host "To upgrade, re-run this installer."
Write-Host "To uninstall: " -NoNewline
Write-Host "Remove-Item -Recurse -Force $AgentcoreHome" -ForegroundColor White

} @args
