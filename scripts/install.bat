@echo off
setlocal EnableDelayedExpansion

REM AgentCore Installer for Windows (cmd.exe / batch)
REM Usage: install.bat [-Version X.Y.Z] [-FromSource] [-SourceDir DIR]
REM                         [-Extras "dev"] [-UvPath PATH] [-Help]
REM
REM Installs AgentCore into %%USERPROFILE%%\.agentcore with a uv-managed Python environment.
REM Users do NOT need Python pre-installed -- uv handles everything.
REM
REM uv is obtained automatically:
REM   1. Found on PATH or in common locations
REM   2. Downloaded via https://astral.sh/uv/install.ps1
REM   3. Downloaded via GitHub Releases if astral.sh is unreachable

REM ── Defaults ──────────────────────────────────────────────────────────────────
if defined AGENTCORE_HOME (
    set "AGENTCORE_HOME=%AGENTCORE_HOME%"
) else (
    set "AGENTCORE_HOME=%USERPROFILE%\.agentcore"
)
set "AGENTCORE_VENV=%AGENTCORE_HOME%\venv"
set "AGENTCORE_BIN=%AGENTCORE_HOME%\bin"
set "PYTHON_VERSION=3.12"
set "AGENTCORE_REPO=https://github.com/your-org/AgentCore.git"

REM ── Argument defaults ─────────────────────────────────────────────────────────
set "ARG_SOURCE_DIR="
set "ARG_EXTRAS="
set "ARG_UV_PATH="
set "CONSOLE_COPIED=0"
set "CONSOLE_AVAILABLE=0"

REM ── Parse arguments ───────────────────────────────────────────────────────────
:parse_args
if "%~1"=="" goto :done_args
if /i "%~1"=="-SourceDir"  goto :arg_sourcedir
if /i "%~1"=="-Extras"     goto :arg_extras
if /i "%~1"=="-UvPath"     goto :arg_uvpath
if /i "%~1"=="-Help"       goto :show_help
shift
goto :parse_args

:arg_sourcedir
set "ARG_SOURCE_DIR=%~2"
shift & shift
goto :parse_args

:arg_extras
set "ARG_EXTRAS=%~2"
shift & shift
goto :parse_args

:arg_uvpath
set "ARG_UV_PATH=%~2"
shift & shift
goto :parse_args

:done_args
goto :main

REM ── Help ──────────────────────────────────────────────────────────────────────
:show_help
echo AgentCore Installer for Windows
echo.
echo Usage: install.bat [OPTIONS]
echo.
echo Options:
echo   -SourceDir ^<DIR^>      Install from local source directory
echo                         (default: auto-clone from GitHub)
echo   -Extras ^<EXTRAS^>      Optional extras (e.g. dev)
echo   -UvPath ^<PATH^>        Path to uv.exe (skips auto-install)
echo   -Help                 Show this help
echo.
echo Environment:
echo   AGENTCORE_HOME        Installation directory (default: %%USERPROFILE%%\.agentcore)
exit /b 0

REM ── Helper functions ──────────────────────────────────────────────────────────
:write_info
echo [agentcore] %~1
exit /b 0

:write_warn
echo [agentcore] WARNING: %~1
exit /b 0

:write_err
echo [agentcore] ERROR: %~1
exit /b 0

:stop_with_error
echo [agentcore] ERROR: %~1
exit /b 1

REM ── Download uv from GitHub Releases ──────────────────────────────────────────
:download_uv_github
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    set "_DL_ARCH=aarch64"
) else (
    set "_DL_ARCH=x86_64"
)
set "_DL_URL=https://github.com/astral-sh/uv/releases/latest/download/uv-!_DL_ARCH!-pc-windows-msvc.zip"
set "_DL_DEST=%LOCALAPPDATA%\uv"
set "_DL_ZIP=%TEMP%\uv-gh-%RANDOM%.zip"

echo [agentcore] Downloading uv ^(!_DL_ARCH!^) from GitHub Releases...

where curl >nul 2>&1
if not errorlevel 1 (
    curl -L --progress-bar -o "!_DL_ZIP!" "!_DL_URL!"
    if not errorlevel 1 goto :download_uv_extract
    echo [agentcore] curl failed, retrying with PowerShell...
    del "!_DL_ZIP!" >nul 2>&1
)

powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '!_DL_URL!' -OutFile '!_DL_ZIP!' -UseBasicParsing"
if errorlevel 1 (
    echo [agentcore] ERROR: GitHub download failed.
    del "!_DL_ZIP!" >nul 2>&1
    exit /b 1
)

:download_uv_extract
if not exist "!_DL_DEST!" mkdir "!_DL_DEST!"
echo [agentcore] Extracting uv...
powershell -NoProfile -Command "Expand-Archive -Force -Path '!_DL_ZIP!' -DestinationPath '!_DL_DEST!'"
set "_DL_ERR=%errorlevel%"
del "!_DL_ZIP!" >nul 2>&1
if %_DL_ERR% neq 0 (
    echo [agentcore] ERROR: Extraction failed.
    exit /b 1
)
if not exist "!_DL_DEST!\uv.exe" (
    echo [agentcore] ERROR: uv.exe not found after extraction.
    exit /b 1
)
set "PATH=!_DL_DEST!;!PATH!"
echo [agentcore] uv installed: !_DL_DEST!\uv.exe
exit /b 0

REM ── Ensure uv ─────────────────────────────────────────────────────────────────
:ensure_uv
REM 0. User-supplied path
if defined ARG_UV_PATH (
    if not exist "%ARG_UV_PATH%" (
        echo [agentcore] ERROR: Specified uv not found: %ARG_UV_PATH%
        exit /b 1
    )
    for %%I in ("%ARG_UV_PATH%") do set "PATH=%%~dpI;!PATH!"
    echo [agentcore] uv found: %ARG_UV_PATH%
    goto :ensure_uv_done
)

REM 1. Already on PATH
where uv >nul 2>&1
if %errorlevel%==0 (
    for /f "delims=" %%p in ('where uv 2^>nul') do (
        echo [agentcore] uv found: %%p
        goto :ensure_uv_done
    )
)

REM 2. Common install locations
for %%c in ("%USERPROFILE%\.local\bin\uv.exe" "%USERPROFILE%\.cargo\bin\uv.exe" "%LOCALAPPDATA%\uv\uv.exe") do (
    if exist %%c (
        for %%d in ("%%~dpc") do set "_UV_DIR=%%~dpc"
        set "PATH=!_UV_DIR!;!PATH!"
        echo [agentcore] uv found: %%~c
        goto :ensure_uv_done
    )
)

REM 3. Try astral.sh
echo [agentcore] Installing uv via astral.sh...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 -TimeoutSec 15 | iex"
if not errorlevel 1 goto :ensure_uv_refresh

REM 4. GitHub Releases fallback
echo [agentcore] astral.sh unreachable, falling back to GitHub Releases...
call :download_uv_github
if errorlevel 1 (
    echo [agentcore] ERROR: Failed to install uv automatically.
    exit /b 1
)
goto :ensure_uv_done

:ensure_uv_refresh
for %%p in ("%USERPROFILE%\.local\bin" "%USERPROFILE%\.cargo\bin" "%LOCALAPPDATA%\uv") do (
    if exist %%p (
        echo "!PATH!" | findstr /i /c:"%%~p" >nul 2>&1
        if errorlevel 1 set "PATH=%%~p;!PATH!"
    )
)
where uv >nul 2>&1
if errorlevel 1 (
    echo [agentcore] ERROR: Failed to install uv.
    exit /b 1
)
echo [agentcore] uv installed via astral.sh

:ensure_uv_done
exit /b 0

REM ── Prepare console frontend ──────────────────────────────────────────────────
:prepare_console
set "_REPO_DIR=%~1"
set "_CONSOLE_SRC=%_REPO_DIR%\console\dist"
REM api.py resolves _CONSOLE_DIST = Path(__file__).parents[2] / "console" / "dist"
REM For venv install, parents[2] is the venv root
set "_CONSOLE_DEST=%AGENTCORE_VENV%\console\dist"

if exist "%_CONSOLE_DEST%\index.html" (
    set "CONSOLE_AVAILABLE=1"
    exit /b 0
)
if exist "%_CONSOLE_SRC%\index.html" (
    echo [agentcore] Copying console frontend assets...
    if not exist "%_CONSOLE_DEST%" mkdir "%_CONSOLE_DEST%"
    xcopy /s /e /y /q "%_CONSOLE_SRC%\*" "%_CONSOLE_DEST%\" >nul
    set "CONSOLE_COPIED=1"
    set "CONSOLE_AVAILABLE=1"
    exit /b 0
)
if not exist "%_REPO_DIR%\console\package.json" (
    echo [agentcore] WARNING: Console source not found.
    exit /b 0
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [agentcore] WARNING: npm not found - skipping console build.
    exit /b 0
)
echo [agentcore] Building console frontend...
pushd "%_REPO_DIR%\console"
npm ci
if errorlevel 1 ( popd & exit /b 0 )
npm run build
if errorlevel 1 ( popd & exit /b 0 )
popd
if exist "%_CONSOLE_SRC%\index.html" (
    if not exist "%_CONSOLE_DEST%" mkdir "%_CONSOLE_DEST%"
    xcopy /s /e /y /q "%_CONSOLE_SRC%\*" "%_CONSOLE_DEST%\" >nul
    set "CONSOLE_COPIED=1"
    set "CONSOLE_AVAILABLE=1"
    echo [agentcore] Console frontend built successfully
)
exit /b 0

:cleanup_console
if "%CONSOLE_COPIED%"=="1" (
    if exist "%AGENTCORE_VENV%\console" rd /s /q "%AGENTCORE_VENV%\console" 2>nul
)
exit /b 0
REM ══════════════════════════════ MAIN ═════════════════════════════════════════
:main
echo [agentcore] Installing AgentCore into %AGENTCORE_HOME%

REM ── Step 1: Ensure uv ─────────────────────────────────────────────────────────
call :ensure_uv
if errorlevel 1 exit /b 1

REM ── Step 2: Create / update virtual environment ───────────────────────────────
if exist "%AGENTCORE_VENV%" (
    echo [agentcore] Existing environment found, upgrading...
) else (
    echo [agentcore] Creating Python %PYTHON_VERSION% environment...
)

uv venv "%AGENTCORE_VENV%" --python %PYTHON_VERSION% --quiet --clear
if errorlevel 1 (
    echo [agentcore] ERROR: Failed to create virtual environment
    exit /b 1
)

set "VENV_PYTHON=%AGENTCORE_VENV%\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo [agentcore] ERROR: Failed to create virtual environment
    exit /b 1
)

for /f "delims=" %%v in ('"%VENV_PYTHON%" --version 2^>^&1') do set "PY_VERSION=%%v"
echo [agentcore] Python environment ready (%PY_VERSION%)

REM ── Step 3: Install AgentCore ─────────────────────────────────────────────────
REM AgentCore is not yet on PyPI, so we always install from source.
REM Either from a local directory (-SourceDir DIR) or by cloning from GitHub.
set "EXTRAS_SUFFIX="
if defined ARG_EXTRAS set "EXTRAS_SUFFIX=[%ARG_EXTRAS%]"

set "VENV_AGENTCORE=%AGENTCORE_VENV%\Scripts\agentcore-api.exe"

if defined ARG_SOURCE_DIR goto :install_from_local
goto :install_from_github

:install_from_local
for %%I in ("%ARG_SOURCE_DIR%") do set "ARG_SOURCE_DIR=%%~fI"
echo [agentcore] Installing AgentCore from local source: %ARG_SOURCE_DIR%
call :prepare_console "%ARG_SOURCE_DIR%"
echo [agentcore] Installing package from source...
uv pip install "%ARG_SOURCE_DIR%%EXTRAS_SUFFIX%" --python "%VENV_PYTHON%"
set "_INST_ERR=%errorlevel%"
call :cleanup_console
if %_INST_ERR% neq 0 (
    echo [agentcore] ERROR: Installation from source failed
    exit /b 1
)
goto :install_verify

:install_from_github
where git >nul 2>&1
if errorlevel 1 (
    echo [agentcore] ERROR: git is required but not found.
    exit /b 1
)
echo [agentcore] Cloning AgentCore from GitHub...
set "CLONE_DIR=%AGENTCORE_HOME%\src"
if exist "%CLONE_DIR%\.git" (
    echo [agentcore] Existing source found, pulling latest changes...
    pushd "%CLONE_DIR%"
    git pull --quiet 2>nul
    popd
) else (
    git clone --depth 1 %AGENTCORE_REPO% "%CLONE_DIR%"
    if errorlevel 1 (
        if exist "%CLONE_DIR%" rd /s /q "%CLONE_DIR%"
        echo [agentcore] ERROR: Failed to clone repository
        exit /b 1
    )
)
call :prepare_console "%CLONE_DIR%"
echo [agentcore] Installing package from source...
uv pip install "%CLONE_DIR%%EXTRAS_SUFFIX%" --python "%VENV_PYTHON%"
set "_INST_ERR=%errorlevel%"
call :cleanup_console
if %_INST_ERR% neq 0 (
    echo [agentcore] ERROR: Installation from source failed
    exit /b 1
)
goto :install_verify

:install_verify
if not exist "%VENV_AGENTCORE%" (
    echo [agentcore] ERROR: Installation failed: agentcore-api not found in venv
    exit /b 1
)
echo [agentcore] AgentCore installed successfully

REM ── Step 4: Create wrapper scripts ────────────────────────────────────────────
if not exist "%AGENTCORE_BIN%" mkdir "%AGENTCORE_BIN%"

REM PowerShell wrapper
set "WRAPPER_PS1=%AGENTCORE_BIN%\agentcore-api.ps1"
echo # AgentCore CLI wrapper > "%WRAPPER_PS1%"
echo $ErrorActionPreference = "Stop" >> "%WRAPPER_PS1%"
echo $AgentcoreHome = if ($env:AGENTCORE_HOME^) { $env:AGENTCORE_HOME ^} else { Join-Path $HOME ".agentcore" ^} >> "%WRAPPER_PS1%"
echo $RealBin = Join-Path $AgentcoreHome "venv\Scripts\agentcore-api.exe" >> "%WRAPPER_PS1%"
echo if (-not (Test-Path $RealBin^)^) { Write-Error "AgentCore not found"; exit 1 ^} >> "%WRAPPER_PS1%"
echo ^& $RealBin @args >> "%WRAPPER_PS1%"
echo [agentcore] PowerShell wrapper created at %WRAPPER_PS1%

REM CMD wrapper
set "WRAPPER_CMD=%AGENTCORE_BIN%\agentcore-api.cmd"
echo @echo off > "%WRAPPER_CMD%"
echo REM AgentCore CLI wrapper >> "%WRAPPER_CMD%"
echo set "AGENTCORE_HOME=%%AGENTCORE_HOME%%" >> "%WRAPPER_CMD%"
echo if "%%AGENTCORE_HOME%%"=="" set "AGENTCORE_HOME=%%USERPROFILE%%\.agentcore" >> "%WRAPPER_CMD%"
echo set "REAL_BIN=%%AGENTCORE_HOME%%\venv\Scripts\agentcore-api.exe" >> "%WRAPPER_CMD%"
echo if not exist "%%REAL_BIN%%" ( echo Error: AgentCore not found ^>&2 ^& exit /b 1 ) >> "%WRAPPER_CMD%"
echo "%%REAL_BIN%%" %%* >> "%WRAPPER_CMD%"
echo [agentcore] CMD wrapper created at %WRAPPER_CMD%

REM ── Step 5: Update PATH ──────────────────────────────────────────────────────
set "CURRENT_USER_PATH="
for /f "skip=2 tokens=1,2,*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do (
    if /i "%%a"=="Path" set "CURRENT_USER_PATH=%%c"
)

set "path_check=;%CURRENT_USER_PATH%;"
set "check_str=;%AGENTCORE_BIN%;"
if /i "%path_check%" neq "%path_check:%check_str%=%" (
    echo [agentcore] %AGENTCORE_BIN% already in PATH
) else (
    if defined CURRENT_USER_PATH (
        powershell -NoProfile -Command "$p = $args[0]; $v = $args[1]; [Environment]::SetEnvironmentVariable('Path', $p + ';' + $v, 'User')" "%AGENTCORE_BIN%" "!CURRENT_USER_PATH!"
    ) else (
        powershell -NoProfile -Command "$p = $args[0]; [Environment]::SetEnvironmentVariable('Path', $p, 'User')" "%AGENTCORE_BIN%"
    )
    if errorlevel 1 (
        echo [agentcore] ERROR: Failed to update PATH.
        exit /b 1
    )
    set "PATH=%AGENTCORE_BIN%;!PATH!"
    echo [agentcore] Added %AGENTCORE_BIN% to PATH
)

REM ── Done ──────────────────────────────────────────────────────────────────────
echo.
echo AgentCore installed successfully!
echo.
echo   Install location:  %AGENTCORE_HOME%
echo   Python:            %PY_VERSION%
if "%CONSOLE_AVAILABLE%"=="1" (
    echo   Console ^(web UI^):  available
) else (
    echo   Console ^(web UI^):  not available
    echo                      Install Node.js and re-run to enable the web UI.
)
echo.
echo To get started, open a new terminal and run:
echo.
echo   agentcore-api        # start AgentCore server
echo.
echo   Default: http://127.0.0.1:8000/
echo.
echo To upgrade, re-run this installer.
echo To uninstall: rmdir /s /q %AGENTCORE_HOME%

exit /b 0
