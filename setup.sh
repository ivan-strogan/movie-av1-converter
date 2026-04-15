#!/usr/bin/env bash
# setup.sh — install dependencies for movie-av1-converter
# Supports macOS (Homebrew) and Ubuntu/Debian Linux

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${BOLD}$*${RESET}"; }
success() { echo -e "${GREEN}✓ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
error()   { echo -e "${RED}✗ $*${RESET}"; exit 1; }

# ── Detect OS ─────────────────────────────────────────────────────────────────
OS="$(uname -s)"
info "Detected OS: $OS"

# ── macOS ─────────────────────────────────────────────────────────────────────
if [ "$OS" = "Darwin" ]; then

    if ! command -v brew &>/dev/null; then
        error "Homebrew not found. Install it from https://brew.sh then re-run this script."
    fi

    info "Installing ffmpeg via Homebrew..."
    brew install ffmpeg python3
    success "ffmpeg and python3 installed"

# ── Linux (Debian/Ubuntu) ─────────────────────────────────────────────────────
elif [ "$OS" = "Linux" ]; then

    if ! command -v apt-get &>/dev/null; then
        error "This script only supports Debian/Ubuntu (apt). Install ffmpeg manually for your distro."
    fi

    info "Updating package lists..."
    sudo apt-get update -qq

    info "Installing python3 and git..."
    sudo apt-get install -y python3 git

    # Check if the system ffmpeg has libsvtav1
    info "Checking ffmpeg for SVT-AV1 support..."
    if apt-cache show ffmpeg &>/dev/null && \
       ffmpeg -encoders 2>/dev/null | grep -q libsvtav1 2>/dev/null; then
        info "Installing ffmpeg from system packages..."
        sudo apt-get install -y ffmpeg
        success "ffmpeg installed with SVT-AV1 support"
    else
        warn "System ffmpeg lacks libsvtav1 — adding ubuntuhandbook1/ffmpeg7 PPA..."
        sudo apt-get install -y software-properties-common
        sudo add-apt-repository -y ppa:ubuntuhandbook1/ffmpeg7
        sudo apt-get update -qq
        sudo apt-get install -y ffmpeg
        success "ffmpeg installed from PPA"
    fi

else
    error "Unsupported OS: $OS. Install ffmpeg and python3 manually."
fi

# ── Verify ffmpeg has libsvtav1 ───────────────────────────────────────────────
info "Verifying SVT-AV1 encoder..."
if ffmpeg -encoders 2>/dev/null | grep -q libsvtav1; then
    success "libsvtav1 encoder is available"
else
    error "libsvtav1 not found in ffmpeg. AV1 encoding will not work."
fi

# ── Verify python3 ───────────────────────────────────────────────────────────
info "Verifying python3..."
PYTHON_VER=$(python3 --version 2>&1)
success "$PYTHON_VER found"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
success "Setup complete. You can now run:"
echo "    python3 main.py scan"
echo "    python3 main.py convert"
