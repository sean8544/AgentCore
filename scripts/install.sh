#!/usr/bin/env bash
# AgentCore Installer for Linux / macOS
# Usage: curl -fsSL <url>/install.sh | bash
#    or: bash install.sh [--version X.Y.Z] [--from-source] [--extras dev]
#
# Installs AgentCore into ~/.agentcore with a uv-managed Python environment.
# Users do NOT need Python pre-installed — uv handles everything.
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
    BOLD="\033[1m"; GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; RESET="\033[0m"
else
    BOLD="" GREEN="" YELLOW="" RED="" RESET=""
fi

info()  { printf "${GREEN}[agentcore]${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}[agentcore]${RESET} %s\n" "$*"; }
error() { printf "${RED}[agentcore]${RESET} %s\n" "$*" >&2; }
die()   { error "$@"; exit 1; }

# ── Defaults ──────────────────────────────────────────────────────────────────
AGENTCORE_HOME="${AGENTCORE_HOME:-$HOME/.agentcore}"
AGENTCORE_VENV="$AGENTCORE_HOME/venv"
AGENTCORE_BIN="$AGENTCORE_HOME/bin"
PYTHON_VERSION="3.12"
AGENTCORE_REPO="https://github.com/your-org/AgentCore.git"

# Intelligent PyPI mirror selection
choose_pypi_mirror() {
    if curl -s --connect-timeout 3 https://pypi.org/simple/ > /dev/null 2>&1; then
        echo "https://pypi.org/simple/"
        info "Using official PyPI source" >&2
    else
        echo "https://mirrors.aliyun.com/pypi/simple/"
        info "Using Aliyun PyPI mirror (official source unreachable)" >&2
    fi
}
PYPI_MIRROR=$(choose_pypi_mirror)

export UV_VENV_CLEAR=1

SOURCE_DIR=""
EXTRAS=""

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-source)
            if [[ $# -ge 2 && "$2" != --* ]]; then
                SOURCE_DIR="$(cd "$2" && pwd)" || die "Directory not found: $2"
                shift
            fi
            shift ;;
        --extras)       EXTRAS="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,10p' "$0"
            echo ""
            echo "Options:"
            echo "  --from-source [DIR]   Install from local source directory"
            echo "                        (default: auto-clone from GitHub)"
            echo "  --extras <EXTRAS>     Comma-separated optional extras (e.g. dev)"
            echo "  -h, --help            Show this help"
            echo ""
            echo "Environment:"
            echo "  AGENTCORE_HOME       Installation directory (default: ~/.agentcore)"
            exit 0 ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
done

# ── OS check ──────────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *) die "Unsupported OS: $OS. This installer supports Linux and macOS only." ;;
esac

printf "${GREEN}[agentcore]${RESET} Installing AgentCore into ${BOLD}%s${RESET}\n" "$AGENTCORE_HOME"

# ── Step 1: Ensure uv ────────────────────────────────────────────────────────
ensure_uv() {
    if command -v uv &>/dev/null; then
        info "uv found: $(command -v uv)"; return
    fi
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            export PATH="$(dirname "$candidate"):$PATH"
            info "uv found: $candidate"; return
        fi
    done
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv &>/dev/null || die "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
    info "uv installed successfully"
}
ensure_uv

# ── Step 2: Create / update virtual environment ──────────────────────────────
if [ -d "$AGENTCORE_VENV" ]; then
    info "Existing environment found, upgrading..."
else
    info "Creating Python $PYTHON_VERSION environment..."
fi
uv venv "$AGENTCORE_VENV" --python "$PYTHON_VERSION" --quiet
[ -x "$AGENTCORE_VENV/bin/python" ] || die "Failed to create virtual environment"
info "Python environment ready ($("$AGENTCORE_VENV/bin/python" --version))"

# ── Step 3: Install AgentCore ─────────────────────────────────────────────────
# AgentCore is not yet on PyPI, so we always install from source.
# Either from a local directory (--from-source DIR) or by cloning from GitHub.
EXTRAS_SUFFIX=""
if [ -n "$EXTRAS" ]; then EXTRAS_SUFFIX="[$EXTRAS]"; fi

_CONSOLE_COPIED=0
_CONSOLE_AVAILABLE=0

prepare_console() {
    local repo_dir="$1"
    local console_src="$repo_dir/console/dist"
    # api.py resolves _CONSOLE_DIST = Path(__file__).parents[2] / "console" / "dist"
    # For venv install, parents[2] is the venv root
    local console_dest="$AGENTCORE_VENV/console/dist"

    if [ -f "$console_dest/index.html" ]; then
        _CONSOLE_AVAILABLE=1; return
    fi
    if [ -d "$console_src" ] && [ -f "$console_src/index.html" ]; then
        info "Copying console frontend assets..."
        mkdir -p "$console_dest"
        cp -R "$console_src/"* "$console_dest/"
        _CONSOLE_COPIED=1; _CONSOLE_AVAILABLE=1; return
    fi
    if [ ! -f "$repo_dir/console/package.json" ]; then
        warn "Console source not found — the web UI won't be available."
        return
    fi
    if ! command -v npm &>/dev/null; then
        warn "npm not found — skipping console frontend build."
        warn "Install Node.js from https://nodejs.org/ then re-run, or build manually."
        return
    fi
    info "Building console frontend (npm ci && npm run build)..."
    (cd "$repo_dir/console" && npm ci && npm run build)
    if [ -f "$console_src/index.html" ]; then
        mkdir -p "$console_dest"
        cp -R "$console_src/"* "$console_dest/"
        _CONSOLE_COPIED=1; _CONSOLE_AVAILABLE=1
        info "Console frontend built successfully"
        return
    fi
    warn "Console build completed but index.html not found — the web UI won't be available."
}

cleanup_console() {
    if [ "$_CONSOLE_COPIED" = 1 ]; then
        rm -rf "$AGENTCORE_VENV/console/"
    fi
}

# Determine source directory
if [ -n "$SOURCE_DIR" ]; then
    # User provided local source path
    info "Installing AgentCore from local source: $SOURCE_DIR"
    prepare_console "$SOURCE_DIR"
    info "Installing package from source..."
    uv pip install "${SOURCE_DIR}${EXTRAS_SUFFIX}" --python "$AGENTCORE_VENV/bin/python" --index-url "$PYPI_MIRROR"
    cleanup_console
else
    # Clone from GitHub
    info "Cloning AgentCore from GitHub..."
    CLONE_DIR="$AGENTCORE_HOME/src"
    if [ -d "$CLONE_DIR/.git" ]; then
        info "Existing source found, pulling latest changes..."
        (cd "$CLONE_DIR" && git pull --quiet) || warn "git pull failed, using existing source"
    else
        mkdir -p "$(dirname "$CLONE_DIR")"
        git clone --depth 1 "$AGENTCORE_REPO" "$CLONE_DIR"
    fi
    prepare_console "$CLONE_DIR"
    info "Installing package from source..."
    uv pip install "${CLONE_DIR}${EXTRAS_SUFFIX}" --python "$AGENTCORE_VENV/bin/python" --index-url "$PYPI_MIRROR"
    cleanup_console
fi

# Verify CLI entry point
[ -x "$AGENTCORE_VENV/bin/agentcore-api" ] || die "Installation failed: agentcore-api CLI not found in venv"
info "AgentCore installed successfully"

# ── Step 4: Create wrapper script ────────────────────────────────────────────
mkdir -p "$AGENTCORE_BIN"

cat > "$AGENTCORE_BIN/agentcore-api" << 'WRAPPER'
#!/usr/bin/env bash
# AgentCore CLI wrapper — delegates to the uv-managed environment.
set -euo pipefail
AGENTCORE_HOME="${AGENTCORE_HOME:-$HOME/.agentcore}"
REAL_BIN="$AGENTCORE_HOME/venv/bin/agentcore-api"
if [ ! -x "$REAL_BIN" ]; then
    echo "Error: AgentCore environment not found at $AGENTCORE_HOME/venv" >&2
    echo "Please reinstall: curl -fsSL <install-url> | bash" >&2
    exit 1
fi
exec "$REAL_BIN" "$@"
WRAPPER
chmod +x "$AGENTCORE_BIN/agentcore-api"
info "Wrapper created at $AGENTCORE_BIN/agentcore-api"

# ── Step 5: Update PATH in shell profile ─────────────────────────────────────
PATH_ENTRY='export PATH="$HOME/.agentcore/bin:$PATH"'

add_to_profile() {
    local profile="$1"
    if [ -f "$profile" ] && grep -qF '.agentcore/bin' "$profile"; then return 0; fi
    if [ -f "$profile" ] || [ "$2" = "create" ]; then
        printf '\n# AgentCore\n%s\n' "$PATH_ENTRY" >> "$profile"
        info "Updated $profile"; return 0
    fi
    return 1
}

UPDATED_PROFILE=false
case "$OS" in
    Darwin)
        add_to_profile "$HOME/.zshrc" "create" && UPDATED_PROFILE=true
        add_to_profile "$HOME/.bash_profile" "no-create" || true ;;
    Linux)
        add_to_profile "$HOME/.bashrc" "create" && UPDATED_PROFILE=true
        add_to_profile "$HOME/.zshrc" "no-create" || true ;;
esac

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
printf "${GREEN}${BOLD}AgentCore installed successfully!${RESET}\n"
echo ""
printf "  Install location:  ${BOLD}%s${RESET}\n" "$AGENTCORE_HOME"
printf "  Python:            ${BOLD}%s${RESET}\n" "$("$AGENTCORE_VENV/bin/python" --version 2>&1)"
if [ "$_CONSOLE_AVAILABLE" = 1 ]; then
    printf "  Console (web UI):  ${GREEN}available${RESET}\n"
else
    printf "  Console (web UI):  ${YELLOW}not available${RESET}\n"
    echo "                     Install Node.js and re-run to enable the web UI."
fi
echo ""

if [ "$UPDATED_PROFILE" = true ]; then
    echo "To get started, open a new terminal or run:"
    echo ""
    printf "  ${BOLD}source ~/.zshrc${RESET}  # or ~/.bashrc\n"
    echo ""
fi

echo "Then run:"
echo ""
printf "  ${BOLD}agentcore-api${RESET}        # start AgentCore server\n"
echo ""
printf "  Default: http://127.0.0.1:8000/\n"
echo ""
printf "To upgrade, re-run this installer.\n"
printf "To uninstall: ${BOLD}rm -rf ~/.agentcore${RESET}\n"
