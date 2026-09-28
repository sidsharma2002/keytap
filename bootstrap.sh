#!/usr/bin/env bash
# keytap bootstrap
# Clones the repo and runs install.sh in one step.
#
# Usage (run this anywhere, no prior clone needed):
#   curl -fsSL https://raw.githubusercontent.com/sidsharma2002/keytap/development/bootstrap.sh | bash

set -euo pipefail

REPO_URL="https://github.com/sidsharma2002/keytap.git"
BRANCH="development"
INSTALL_DIR="$HOME/keytap"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[keytap]${NC} $*"; }
warn()  { echo -e "${YELLOW}[keytap]${NC} $*"; }
error() { echo -e "${RED}[keytap]${NC} $*"; }

# ── git ───────────────────────────────────────────────────────────────────────

if ! command -v git &>/dev/null; then
    error "git not found. Install Xcode Command Line Tools: xcode-select --install"
    exit 1
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Repo already exists at $INSTALL_DIR — pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "Cloning keytap into $INSTALL_DIR..."
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# ── install ───────────────────────────────────────────────────────────────────

bash "$INSTALL_DIR/install.sh"
