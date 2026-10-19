#!/usr/bin/env bash
# keytap install script
# Installs Python dependencies, checks system tools, registers the
# `keytap` shell command in your ~/.zshrc or ~/.bash_profile, and installs the
# Claude skills (harden-recording, harden-suite, steer-phone) into ~/.claude/skills so it works from any folder.
#
# Usage:
#   bash install.sh               # full install
#   bash install.sh --skill-only  # only (re)install the Claude skill, e.g. after editing it
#
# After install, open a new terminal and run:
#   keytap

set -euo pipefail

KEYTAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# keytap runs from its own virtualenv so it never touches (or depends on) the system
# Python. Override with KEYTAP_VENV; keytap/_venv.py reads the same variable.
VENV_DIR="${KEYTAP_VENV:-$HOME/.keytap/venv}"
VENV_PY="$VENV_DIR/bin/python"
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[keytap]${NC} $*"; }
warn()  { echo -e "${YELLOW}[keytap]${NC} $*"; }
error() { echo -e "${RED}[keytap]${NC} $*"; }

# ── Claude skill ──────────────────────────────────────────────────────────────

# Copies every folder in skills/ to ~/.claude/skills and writes this keytap folder's path into
# it, so Claude can find replay.py when it is not started inside the keytap folder.
install_skill() {
    local src name dst skill_py
    if [[ -x "$VENV_PY" ]]; then skill_py="$VENV_PY"; else skill_py="$(command -v python3 || echo python3)"; fi
    for src in "$KEYTAP_DIR"/skills/*/; do
        [[ -f "$src/SKILL.md" ]] || continue
        name="$(basename "$src")"
        dst="$HOME/.claude/skills/$name"
        rm -rf "$dst"
        mkdir -p "$dst"
        cp -R "$src." "$dst/"
        find "$dst" -name '*.md' -exec sed -i.bak -e "s|__KEYTAP_DIR__|$KEYTAP_DIR|g" -e "s|__KEYTAP_PYTHON__|$skill_py|g" {} +
        find "$dst" -name '*.bak' -delete
        info "Installed Claude skill $name to $dst"
    done
}

if [[ "${1:-}" == "--skill-only" ]]; then
    install_skill
    exit 0
fi

# ── Python ────────────────────────────────────────────────────────────────────

MIN_PY_MAJOR=3
MIN_PY_MINOR=10

# Prints "major.minor" for the given interpreter, or nothing if it cannot run.
py_version() {
    "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true
}

py_is_supported() {
    "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)" 2>/dev/null
}

# The command palette is a tkinter window. tkinter ships with the Python build rather
# than pip, and many Homebrew/Debian Pythons leave it out.
py_has_tkinter() {
    "$1" -c "import tkinter" 2>/dev/null
}

# Pick the first interpreter on PATH that is new enough and has tkinter. Newer versioned
# names go first so a machine with an old default python3 (e.g. macOS 3.9) still works.
PYTHON=""
FOUND_OLD=""
FOUND_NO_TK=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
    path=$(command -v "$cand" || true)
    [[ -n "$path" ]] || continue
    if ! py_is_supported "$path"; then
        [[ -n "$FOUND_OLD" ]] || FOUND_OLD="$path ($(py_version "$path"))"
    elif ! py_has_tkinter "$path"; then
        [[ -n "$FOUND_NO_TK" ]] || FOUND_NO_TK="$path ($(py_version "$path"))"
    else
        PYTHON="$path"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    if [[ -n "$FOUND_NO_TK" ]]; then
        error "Found $FOUND_NO_TK, but it was built without tkinter (needed for the command palette)."
        echo "  Install Tk for that Python, then re-run this script:"
        echo "    macOS:         brew install python-tk@$(py_version "${FOUND_NO_TK%% *}")"
        echo "    Debian/Ubuntu: sudo apt install python3-tk"
        echo "    Fedora:        sudo dnf install python3-tkinter"
        exit 1
    fi
    if [[ -n "$FOUND_OLD" ]]; then
        error "Python $MIN_PY_MAJOR.$MIN_PY_MINOR or newer is required, but only found: $FOUND_OLD"
    else
        error "Python $MIN_PY_MAJOR.$MIN_PY_MINOR or newer is required, but no python3 was found."
    fi
    echo "  Install a newer Python, then re-run this script:"
    echo "    macOS:         brew install python@3.12 python-tk@3.12"
    echo "    Debian/Ubuntu: sudo apt install python3 python3-pip python3-venv python3-tk"
    echo "    Fedora:        sudo dnf install python3 python3-pip python3-tkinter"
    echo "    Other:         https://www.python.org/downloads/"
    exit 1
fi
info "Python $(py_version "$PYTHON") found at $PYTHON"

# ── virtualenv + pip install ──────────────────────────────────────────────────

# A dedicated venv sidesteps PEP 668 ("externally managed") errors, avoids version
# clashes with other tools, and means `keytap` always runs the Python it was installed with.
if [[ -x "$VENV_PY" ]] && { ! py_is_supported "$VENV_PY" || ! py_has_tkinter "$VENV_PY"; }; then
    warn "Existing virtualenv at $VENV_DIR uses an unsupported Python (or one without tkinter) — recreating it"
    rm -rf "$VENV_DIR"
fi

if [[ ! -x "$VENV_PY" ]]; then
    info "Creating virtualenv at $VENV_DIR..."
    mkdir -p "$(dirname "$VENV_DIR")"
    if ! "$PYTHON" -m venv "$VENV_DIR"; then
        rm -rf "$VENV_DIR"
        error "Could not create a virtualenv with $PYTHON."
        echo "  On Debian/Ubuntu install the venv module first: sudo apt install python3-venv"
        exit 1
    fi
else
    info "Using existing virtualenv at $VENV_DIR"
fi

# Console scripts such as mitmdump live here.
export PATH="$VENV_DIR/bin:$PATH"

LOG_FILE="${KEYTAP_LOG:-$(dirname "$VENV_DIR")/install.log}"
{ echo "=== keytap install $(date) ==="; echo "python: $PYTHON ($(py_version "$PYTHON"))"; } >> "$LOG_FILE"

# pip install into the venv with output going to the log; on failure show the tail of it.
pip_install() {
    local start
    start=$(wc -l < "$LOG_FILE")
    if "$VENV_PY" -m pip install "$@" >> "$LOG_FILE" 2>&1; then
        return 0
    fi
    echo "  ---- last lines of pip output ----"
    tail -n +"$((start + 1))" "$LOG_FILE" | tail -n 25 | cut -c1-200 | sed 's/^/  | /'
    echo "  ----------------------------------"
    return 1
}

# Needed for keytap to run at all: a failure here stops the install.
CORE_PKGS=("av>=18.0" "numpy>=1.24" "Pillow>=9.0" "pygame>=2.0" "uiautomator2>=2.0")
# Each only enables one feature (imported lazily), so a failure only disables that feature.
OPTIONAL_PKGS=("mitmproxy>=10.0|network capture and replay" "perfetto>=0.5|the profiler")

info "Installing Python dependencies (full log: $LOG_FILE)..."
pip_install --upgrade pip || warn "Could not upgrade pip; continuing with the bundled one"

if ! pip_install "${CORE_PKGS[@]}"; then
    error "Installing the core Python dependencies failed."
    echo "  Common causes: no network access, or no pre-built wheel for Python $(py_version "$PYTHON") on this platform."
    echo "  Try another Python version (3.11 or 3.12 have the widest wheel support), then re-run. Log: $LOG_FILE"
    exit 1
fi
info "Core dependencies installed."

SKIPPED_OPTIONAL=()
for entry in "${OPTIONAL_PKGS[@]}"; do
    pkg="${entry%%|*}"
    feature="${entry#*|}"
    if pip_install "$pkg"; then
        info "Installed optional $pkg"
    else
        warn "Could not install $pkg — $feature will not work. See $LOG_FILE"
        SKIPPED_OPTIONAL+=("$pkg")
    fi
done

# ── System tools ──────────────────────────────────────────────────────────────

check_tool() {
    local name="$1" hint="$2"
    if command -v "$name" &>/dev/null; then
        info "$name found at $(command -v "$name")"
    else
        warn "$name not found. $hint"
    fi
}

check_tool adb    "Install Android SDK platform-tools or run: brew install android-platform-tools"
check_tool ffmpeg "Install ffmpeg (optional, used by screenrecord fallback): brew install ffmpeg"

# ── mitmproxy CA cert (required for network capture) ──────────────────────────

MITM_CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
if ! command -v mitmdump &>/dev/null; then
    warn "mitmproxy is not installed, skipping the network-capture certificate setup."
elif [[ ! -f "$MITM_CERT" ]]; then
    info "Generating mitmproxy CA certificate..."
    timeout 2 mitmdump --quiet 2>/dev/null || true
    if [[ -f "$MITM_CERT" ]]; then
        info "CA cert generated at $MITM_CERT"
    else
        warn "Could not generate mitmproxy cert automatically. Run 'mitmdump' once manually."
    fi
else
    info "mitmproxy CA cert already exists at $MITM_CERT"
fi

if [[ -f "$MITM_CERT" ]]; then
    echo ""
    warn "Network capture requires the mitmproxy CA cert installed on your Android device."
    echo "  One-time device setup (debug apps only):"
    echo ""
    echo "  1. Push cert to device:"
    echo "       adb push $MITM_CERT /sdcard/mitmproxy-ca.pem"
    echo "  2. On device: Settings > Security > Install certificate > CA certificate"
    echo "     Select mitmproxy-ca.pem from Internal Storage"
    echo "  3. Your app's network_security_config.xml must allow user CAs:"
    echo "       <debug-overrides>"
    echo "           <trust-anchors>"
    echo "               <certificates src=\"user\" />"
    echo "           </trust-anchors>"
    echo "       </debug-overrides>"
    echo ""
fi

# ── Shell function ────────────────────────────────────────────────────────────

KEYTAP_FUNC=$(cat <<EOF

# keytap — keyboard-driven Android mirror
keytap() {
    "$VENV_PY" "$KEYTAP_DIR/keytap.py" "\$@"
}
EOF
)

add_to_shell() {
    local rc_file="$1"
    if [[ -f "$rc_file" ]]; then
        if grep -q "keytap()" "$rc_file" 2>/dev/null; then
            # Update the path in case repo moved
            sed -i.bak "/python.*keytap\.py/c\\    \"$VENV_PY\" \"$KEYTAP_DIR/keytap.py\" \"\\\$@\"" "$rc_file"
            info "Updated keytap path in $rc_file"
        else
            echo "$KEYTAP_FUNC" >> "$rc_file"
            info "Added keytap function to $rc_file"
        fi
        return 0
    fi
    return 1
}

ADDED=0
# Prefer zshrc on macOS, fall back to bash_profile, then bashrc
for RC in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc"; do
    if add_to_shell "$RC"; then
        ADDED=1
        break
    fi
done

if [[ $ADDED -eq 0 ]]; then
    # None exist yet — create .zshrc
    touch "$HOME/.zshrc"
    echo "$KEYTAP_FUNC" >> "$HOME/.zshrc"
    info "Created ~/.zshrc with keytap function"
fi

install_skill

# ── Smoke test ────────────────────────────────────────────────────────────────

info "Checking the install..."
SMOKE_OK=1
SMOKE_OUT=$(cd "$KEYTAP_DIR" && "$VENV_PY" -m keytap.smoke 2>&1) || SMOKE_OK=0
echo "$SMOKE_OUT" | tee -a "$LOG_FILE"
if [[ $SMOKE_OK -eq 0 ]]; then
    error "The install finished, but keytap cannot start (see the FAIL lines above). Log: $LOG_FILE"
    exit 1
fi
if [[ ${#SKIPPED_OPTIONAL[@]} -gt 0 ]]; then
    warn "Skipped optional packages: ${SKIPPED_OPTIONAL[*]}"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
info "Install complete."
echo ""
echo "  Open a new terminal and run:"
echo ""
echo "      keytap"
echo ""
echo "  Options:"
echo "      keytap --height 1000          # taller mirror window"
echo "      keytap --backend screenrecord # fallback capture"
echo "      keytap --serial emulator-5554 # target specific device"
echo ""
echo "  Claude skill (harden a recording, build a test suite): in Claude Code, ask it to harden a keytap recording or build a suite,"
echo "  or run: ./replay.py --help"
echo ""
