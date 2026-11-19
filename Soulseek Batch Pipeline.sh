#!/bin/bash
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$PROJECT_DIR/bin:$PROJECT_DIR/runtime/python/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

PYTHON_BIN=""
for candidate in \
    "$PROJECT_DIR/runtime/python/bin/python3" \
    "$(command -v python3 2>/dev/null || true)" \
    "/usr/local/bin/python3" \
    "/usr/bin/python3"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERRORE: Python 3 (>=3.9) non trovato. Riesegui install.sh."
    [ -t 0 ] && read -r -p "Premi Invio per chiudere..."
    exit 1
fi

launch_in_terminal() {
    local self_path="$PROJECT_DIR/Soulseek Batch Pipeline.sh"
    local args=("$@")
    export SLSK_IN_TERMINAL=1

    if command -v x-terminal-emulator >/dev/null 2>&1; then
        exec x-terminal-emulator -e "$self_path" --pause "${args[@]}"
    elif command -v gnome-terminal >/dev/null 2>&1; then
        exec gnome-terminal -- "$self_path" --pause "${args[@]}"
    elif command -v konsole >/dev/null 2>&1; then
        exec konsole -e "$self_path" --pause "${args[@]}"
    elif command -v xfce4-terminal >/dev/null 2>&1; then
        exec xfce4-terminal -x "$self_path" --pause "${args[@]}"
    elif command -v kitty >/dev/null 2>&1; then
        exec kitty "$self_path" --pause "${args[@]}"
    elif command -v alacritty >/dev/null 2>&1; then
        exec alacritty -e "$self_path" --pause "${args[@]}"
    elif command -v foot >/dev/null 2>&1; then
        exec foot "$self_path" --pause "${args[@]}"
    elif command -v wezterm >/dev/null 2>&1; then
        exec wezterm start -- "$self_path" --pause "${args[@]}"
    elif command -v xterm >/dev/null 2>&1; then
        exec xterm -e "$self_path" --pause "${args[@]}"
    fi
}

# If invoked from a .desktop launcher or soulseekdl:// protocol handler without a TTY,
# open a native terminal so download progress, track checks, and tagger output remain visible.
if [ ! -t 0 ] && [ ! -t 1 ] && [ -z "${SLSK_IN_TERMINAL:-}" ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
    launch_in_terminal "$@"
fi

if [ $# -eq 0 ]; then
    exec "$PYTHON_BIN" "$PROJECT_DIR/src/cli.py" --pause
else
    exec "$PYTHON_BIN" "$PROJECT_DIR/src/cli.py" "$@"
fi
