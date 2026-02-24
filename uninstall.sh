#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/agentmesh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }

# ------------------------------------------------------------------
# 1. Remove symlinks from /usr/local/bin
# ------------------------------------------------------------------
for cmd in amesh amesh-mcp; do
    link="/usr/local/bin/$cmd"
    if [ -L "$link" ]; then
        rm -f "$link" 2>/dev/null && info "Removed symlink $link" \
            || warn "Could not remove $link (may need sudo: sudo rm $link)"
    fi
done

# ------------------------------------------------------------------
# 2. Remove install directory
# ------------------------------------------------------------------
if [ -d "$INSTALL_DIR" ]; then
    info "Removing $INSTALL_DIR ..."
    rm -rf "$INSTALL_DIR"
    info "Removed $INSTALL_DIR"
else
    warn "$INSTALL_DIR does not exist, nothing to remove."
fi

# ------------------------------------------------------------------
# 3. Done
# ------------------------------------------------------------------
echo ""
info "Uninstall complete."
