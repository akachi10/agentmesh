#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/agentmesh"
BIN_DIR="$INSTALL_DIR/bin"
VENV_DIR="$INSTALL_DIR/venv"
SOCK_DIR="$INSTALL_DIR/sock"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ------------------------------------------------------------------
# 1. Check prerequisites
# ------------------------------------------------------------------
info "Checking prerequisites..."

# Python >= 3.12 — try python3.12 first, then python3
PYTHON=""
for candidate in python3.12 python3; do
    if command -v "$candidate" &>/dev/null; then
        PY_VERSION=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python >= 3.12 not found. Please install Python 3.12+."
fi
info "Using $PYTHON ($PY_VERSION)"

# claude CLI
if ! command -v claude &>/dev/null; then
    warn "claude CLI not found. AgentMesh requires Claude Code to run."
    warn "Install with: npm install -g @anthropic-ai/claude-code"
fi

# tmux >= 3.3
_need_tmux_install=false
if command -v tmux &>/dev/null; then
    TMUX_VERSION=$(tmux -V | sed 's/tmux //')
    TMUX_MAJOR=$(echo "$TMUX_VERSION" | cut -d. -f1)
    TMUX_MINOR=$(echo "$TMUX_VERSION" | cut -d. -f2 | sed 's/[^0-9].*//')
    if [ "$TMUX_MAJOR" -lt 3 ] || { [ "$TMUX_MAJOR" -eq 3 ] && [ "$TMUX_MINOR" -lt 3 ]; }; then
        warn "tmux >= 3.3 required (found $TMUX_VERSION), will try to upgrade..."
        _need_tmux_install=true
    else
        info "tmux $TMUX_VERSION found"
    fi
else
    warn "tmux not found, will try to install..."
    _need_tmux_install=true
fi

if [ "$_need_tmux_install" = true ]; then
    if command -v brew &>/dev/null; then
        info "Installing tmux via Homebrew..."
        brew install tmux || brew upgrade tmux || error "Failed to install tmux via Homebrew."
    elif command -v apt-get &>/dev/null; then
        info "Installing tmux via apt-get..."
        sudo apt-get update -qq && sudo apt-get install -y tmux || error "Failed to install tmux via apt-get."
    elif command -v yum &>/dev/null; then
        info "Installing tmux via yum..."
        sudo yum install -y tmux || error "Failed to install tmux via yum."
    else
        error "Cannot auto-install tmux: no supported package manager (brew/apt/yum). Please install tmux >= 3.3 manually."
    fi
    # Verify installed version
    if ! command -v tmux &>/dev/null; then
        error "tmux installation failed."
    fi
    TMUX_VERSION=$(tmux -V | sed 's/tmux //')
    TMUX_MAJOR=$(echo "$TMUX_VERSION" | cut -d. -f1)
    TMUX_MINOR=$(echo "$TMUX_VERSION" | cut -d. -f2 | sed 's/[^0-9].*//')
    if [ "$TMUX_MAJOR" -lt 3 ] || { [ "$TMUX_MAJOR" -eq 3 ] && [ "$TMUX_MINOR" -lt 3 ]; }; then
        error "tmux >= 3.3 required but package manager installed $TMUX_VERSION. Please upgrade tmux manually."
    fi
    info "tmux $TMUX_VERSION installed successfully"
fi

# ------------------------------------------------------------------
# 2. Create directory structure
# ------------------------------------------------------------------
info "Creating directory structure at $INSTALL_DIR ..."
mkdir -p "$BIN_DIR" "$SOCK_DIR"

# ------------------------------------------------------------------
# 3. Create venv + install package
# ------------------------------------------------------------------
if [ -d "$VENV_DIR" ]; then
    info "Existing venv found, upgrading..."
else
    info "Creating Python virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

info "Installing agentmesh from source ($SCRIPT_DIR) ..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install "$SCRIPT_DIR" --quiet

# ------------------------------------------------------------------
# 4. Create wrapper scripts
# ------------------------------------------------------------------
info "Creating wrapper scripts in $BIN_DIR ..."

cat > "$BIN_DIR/amesh" << 'WRAPPER'
#!/usr/bin/env bash
INSTALL_DIR="$HOME/agentmesh"
exec "$INSTALL_DIR/venv/bin/amesh" "$@"
WRAPPER
chmod +x "$BIN_DIR/amesh"

cat > "$BIN_DIR/amesh-mcp" << 'WRAPPER'
#!/usr/bin/env bash
INSTALL_DIR="$HOME/agentmesh"
exec "$INSTALL_DIR/venv/bin/amesh-mcp" "$@"
WRAPPER
chmod +x "$BIN_DIR/amesh-mcp"

# ------------------------------------------------------------------
# 5. Symlink to /usr/local/bin (optional)
# ------------------------------------------------------------------
SYMLINK_TARGET="/usr/local/bin/amesh"
SYMLINK_MCP_TARGET="/usr/local/bin/amesh-mcp"

create_symlinks() {
    ln -sf "$BIN_DIR/amesh" "$SYMLINK_TARGET" 2>/dev/null && \
    ln -sf "$BIN_DIR/amesh-mcp" "$SYMLINK_MCP_TARGET" 2>/dev/null
}

if create_symlinks; then
    info "Symlinks created in /usr/local/bin/"
else
    warn "Could not create symlinks in /usr/local/bin/ (may need sudo)."
fi

# ------------------------------------------------------------------
# 5.1 Write PATH to shell profile
# ------------------------------------------------------------------
PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""

# Detect shell profile
if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-/bin/sh}")" = "zsh" ]; then
    PROFILE="$HOME/.zshrc"
elif [ -f "$HOME/.bash_profile" ]; then
    PROFILE="$HOME/.bash_profile"
else
    PROFILE="$HOME/.profile"
fi

if grep -qF "$BIN_DIR" "$PROFILE" 2>/dev/null; then
    info "PATH already configured in $PROFILE"
else
    echo "" >> "$PROFILE"
    echo "# AgentMesh" >> "$PROFILE"
    echo "$PATH_LINE" >> "$PROFILE"
    info "PATH added to $PROFILE (restart terminal or run: source $PROFILE)"
fi

# ------------------------------------------------------------------
# 6. Done
# ------------------------------------------------------------------
echo ""
info "Installation complete!"
echo ""
echo "  Install directory:  $INSTALL_DIR"
echo "  Commands:           amesh, amesh-mcp"
echo ""
echo "  Usage:"
echo "    amesh              # Start an AgentMesh instance"
echo ""
echo "  Uninstall:"
echo "    bash $SCRIPT_DIR/uninstall.sh"
echo ""
