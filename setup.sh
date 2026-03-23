#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "=== Whisper Dictate Setup ==="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10+."
    exit 1
fi

# Check tkinter (required by customtkinter)
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "ERROR: tkinter not found. Install it with your package manager:"
    echo "  Fedora/RHEL:  sudo dnf install python3-tkinter"
    echo "  Ubuntu/Debian: sudo apt install python3-tk"
    echo "  Arch:          sudo pacman -S tk"
    exit 1
fi

# Create venv and install Python dependencies
echo "[1/2] Creating virtual environment and installing Python packages..."
python3 -m venv .venv
.venv/bin/pip install --quiet -r requirements.txt

echo "[2/2] Checking system dependencies..."
echo ""

WARNINGS=0

# Clipboard tool
if [ "$XDG_SESSION_TYPE" = "wayland" ]; then
    if ! command -v wl-copy &>/dev/null; then
        echo "WARNING: wl-copy not found (needed for clipboard on Wayland)"
        echo "  Install: sudo dnf install wl-clipboard  # or apt install wl-clipboard"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    if ! command -v xclip &>/dev/null && ! command -v xsel &>/dev/null; then
        echo "WARNING: xclip/xsel not found (needed for clipboard on X11)"
        echo "  Install: sudo dnf install xclip  # or apt install xclip"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

# Auto-paste tool (for --quick mode)
if command -v ydotool &>/dev/null; then
    # Check if daemon is running and accessible
    if ! YDOTOOL_SOCKET=/tmp/.ydotool_socket ydotool key 0:0 2>/dev/null; then
        echo "WARNING: ydotool is installed but its daemon may not be running or accessible."
        echo "  Start daemon:     sudo systemctl enable --now ydotool"
        echo "  Fix permissions:  See README.md for ydotool socket setup"
        WARNINGS=$((WARNINGS + 1))
    fi
elif [ "$XDG_SESSION_TYPE" != "wayland" ] && command -v xdotool &>/dev/null; then
    : # xdotool available for X11, ok
else
    echo "WARNING: No auto-paste tool found (needed for --quick mode)"
    if [ "$XDG_SESSION_TYPE" = "wayland" ]; then
        echo "  Install: sudo dnf install ydotool  # or apt install ydotool"
    else
        echo "  Install: sudo dnf install xdotool  # or apt install xdotool"
    fi
    echo "  Without this, --quick mode will copy to clipboard but not auto-paste."
    WARNINGS=$((WARNINGS + 1))
fi

echo ""
if [ "$WARNINGS" -gt 0 ]; then
    echo "Setup complete with $WARNINGS warning(s). See above."
else
    echo "Setup complete. All dependencies found."
fi
echo ""
echo "Run with:  .venv/bin/python dictate.py"
echo "Quick:     .venv/bin/python dictate.py --quick --lang en"
