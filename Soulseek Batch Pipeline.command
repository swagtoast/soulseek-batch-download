#!/bin/bash
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$PROJECT_DIR/bin:$PROJECT_DIR/runtime/python/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PYTHON_BIN=""
for candidate in \
    "$PROJECT_DIR/runtime/python/bin/python3" \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3" \
    "/usr/bin/python3"; do
    if [ -x "$candidate" ]; then
        if [ "$candidate" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERRORE: Python 3 non trovato. Riesegui install.sh."
    read -r -p "Premi Invio per chiudere..."
    exit 1
fi

if [ $# -eq 0 ]; then
    exec "$PYTHON_BIN" "$PROJECT_DIR/src/cli.py" --pause
else
    exec "$PYTHON_BIN" "$PROJECT_DIR/src/cli.py" "$@"
fi
