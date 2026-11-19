#!/bin/bash
set -euo pipefail

REPO_SLUG="${SLSK_REPO_SLUG:-swagtoast/soulseek-batch-download}"
REPO_BRANCH="${SLSK_REPO_BRANCH:-main}"
INSTALL_DIR="${SLSK_INSTALL_DIR:-$HOME/Applications/Soulseek Batch Download}"
SERVICES_DIR="${SLSK_SERVICES_DIR:-$HOME/Library/Services}"
SOCKSEEK_VERSION="3.0.6"
FFMPEG_RELEASE="b6.1.1"
PYTHON_STANDALONE_TAG="20261110"
PYTHON_STANDALONE_VER="3.12.16"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "Errore: questo installer è pensato per macOS."
    exit 1
fi

# Clean up any stale legacy JXA osascript processes from older installs (e.g. Intel Air 2019).
pkill -f "slsk_setup\.js|slsk_prompt\.js" 2>/dev/null || true

ARCH="$(uname -m)"
MACOS_VER="$(sw_vers -productVersion 2>/dev/null || echo "unknown")"
CPU_CORES="$(sysctl -n hw.ncpu 2>/dev/null || echo 2)"
RAM_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo 8589934592)"
RAM_GB="$(( (RAM_BYTES + 536870912) / 1073741824 ))"

case "$ARCH" in
    arm64)
        SOCKSEEK_ARCH="osx-arm64"
        DENO_ARCH="aarch64-apple-darwin"
        FFMPEG_ARCH="darwin-arm64"
        PY_ARCH="aarch64-apple-darwin"
        CHIP_LABEL="Apple Silicon (arm64)"
        ;;
    x86_64)
        SOCKSEEK_ARCH="osx-x64"
        DENO_ARCH="x86_64-apple-darwin"
        FFMPEG_ARCH="darwin-x64"
        PY_ARCH="x86_64-apple-darwin"
        CHIP_LABEL="Intel (x86_64)"
        ;;
    *)
        echo "Errore: architettura non supportata: $ARCH"
        exit 1
        ;;
esac

# Choose optimal native UI engine based on Mac specs:
# - Apple Silicon OR strong Intel (>=6 cores & >=16 GB RAM) with Xcode CLT: compile single-window Swift AppKit UI.
# - Lighter/older Intel Macs (e.g. 2018-2020 Air 8 GB) or Macs without Xcode CLT: skip Swift build (0s wait)
#   and use zero-CPU StandardAdditions native macOS dialogs (display dialog / Finder choose folder).
PREFER_SWIFT_UI="0"
if [ "$ARCH" = "arm64" ] || { [ "$CPU_CORES" -ge 6 ] && [ "$RAM_GB" -ge 16 ]; }; then
    PREFER_SWIFT_UI="1"
fi

echo ""
echo "=========================================="
echo "  Soulseek Batch Download — Installer Mac"
echo "=========================================="
echo "Profilo Mac: $CHIP_LABEL | macOS $MACOS_VER | ${CPU_CORES} core | ${RAM_GB} GB RAM"
echo "Installazione in: $INSTALL_DIR"
echo ""

is_valid_bin() {
    [ -x "$1" ] && [ -s "$1" ]
}

TMP_WORK="$(mktemp -d /tmp/slsk_install_XXXXXX)"
cleanup() { rm -rf "$TMP_WORK"; }
trap cleanup EXIT

mkdir -p "$INSTALL_DIR/bin" "$INSTALL_DIR/config" "$INSTALL_DIR/lists"

echo "[1/6] Scarico l'app da GitHub ($REPO_SLUG)..."
curl -fL --progress-bar "https://github.com/$REPO_SLUG/archive/refs/heads/$REPO_BRANCH.tar.gz" -o "$TMP_WORK/repo.tar.gz"
tar -xzf "$TMP_WORK/repo.tar.gz" -C "$TMP_WORK"
SRC_ROOT="$(find "$TMP_WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

rm -rf "$INSTALL_DIR/src" "$INSTALL_DIR/tests" "$INSTALL_DIR/assets"
cp -R "$SRC_ROOT/src" "$INSTALL_DIR/src"
[ -d "$SRC_ROOT/tests" ] && cp -R "$SRC_ROOT/tests" "$INSTALL_DIR/tests"
[ -d "$SRC_ROOT/assets" ] && cp -R "$SRC_ROOT/assets" "$INSTALL_DIR/assets"
cp "$SRC_ROOT/Soulseek Batch Pipeline.command" "$INSTALL_DIR/Soulseek Batch Pipeline.command"
cp "$SRC_ROOT/README.md" "$INSTALL_DIR/README.md"
[ -f "$SRC_ROOT/config/sockseek.conf.example" ] && cp "$SRC_ROOT/config/sockseek.conf.example" "$INSTALL_DIR/config/"
[ -f "$SRC_ROOT/config/pipeline.json.example" ] && cp "$SRC_ROOT/config/pipeline.json.example" "$INSTALL_DIR/config/"
chmod +x "$INSTALL_DIR/Soulseek Batch Pipeline.command"

# Apply custom Swagtoast + Soulseek icon to Soulseek Batch Pipeline.command (synchronous NSWorkspace call, ~0.2s).
if [ -f "$INSTALL_DIR/assets/app_icon.png" ] && [ -x "/usr/bin/osascript" ]; then
    /usr/bin/osascript -l JavaScript -e '
ObjC.import("AppKit");
function run(argv) {
  var img = $.NSImage.alloc.initWithContentsOfFile(argv[0]);
  if (img && !img.isNil()) {
    $.NSWorkspace.sharedWorkspace.setIconForFileOptions(img, argv[1], 0);
  }
}
' "$INSTALL_DIR/assets/app_icon.png" "$INSTALL_DIR/Soulseek Batch Pipeline.command" >/dev/null 2>&1 || true
fi

# Seed auto-updater state with the installed commit SHA so the very first launch doesn't re-download immediately.
INSTALLED_SHA="$(curl -fsSL --max-time 3 -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/$REPO_SLUG/commits/$REPO_BRANCH" 2>/dev/null || true)"
if [ "${#INSTALLED_SHA}" -eq 40 ]; then
    printf '{"commit": "%s", "last_check": %s}\n' "$INSTALLED_SHA" "$(date +%s)" > "$INSTALL_DIR/config/.update_state.json"
fi

echo "[2/6] Verifico Python 3..."
find_working_python() {
    for p in \
        "$INSTALL_DIR/runtime/python/bin/python3" \
        "/opt/homebrew/bin/python3" \
        "/usr/local/bin/python3" \
        "/usr/bin/python3"; do
        [ -x "$p" ] || continue
        if [ "$p" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$p"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(find_working_python || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "      Scarico Python 3 standalone (nessun Homebrew necessario)..."
    PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_TAG}/cpython-${PYTHON_STANDALONE_VER}%2B${PYTHON_STANDALONE_TAG}-${PY_ARCH}-install_only_stripped.tar.gz"
    mkdir -p "$INSTALL_DIR/runtime"
    curl -fL --progress-bar "$PY_URL" -o "$TMP_WORK/python.tar.gz"
    rm -rf "$INSTALL_DIR/runtime/python"
    tar -xzf "$TMP_WORK/python.tar.gz" -C "$INSTALL_DIR/runtime"
    xattr -dr com.apple.quarantine "$INSTALL_DIR/runtime/python" 2>/dev/null || true
    PYTHON_BIN="$INSTALL_DIR/runtime/python/bin/python3"
fi
echo "      Python pronto: $PYTHON_BIN"

echo "[3/6] Preparo Sockseek v$SOCKSEEK_VERSION ($SOCKSEEK_ARCH)..."
if is_valid_bin "$INSTALL_DIR/bin/sockseek"; then
    echo "      sockseek già presente."
else
    SOCKSEEK_URL="https://github.com/fiso64/sockseek/releases/download/v${SOCKSEEK_VERSION}/sockseek_${SOCKSEEK_VERSION}_${SOCKSEEK_ARCH}.tar.gz"
    curl -fL --progress-bar "$SOCKSEEK_URL" -o "$TMP_WORK/sockseek.tar.gz"
    tar -xzf "$TMP_WORK/sockseek.tar.gz" -C "$INSTALL_DIR/bin"
    chmod +x "$INSTALL_DIR/bin/sockseek"
fi

echo "[4/6] Preparo yt-dlp e deno (per YouTube)..."
if is_valid_bin "$INSTALL_DIR/bin/yt-dlp" || command -v yt-dlp >/dev/null 2>&1 || [ -x "/opt/homebrew/bin/yt-dlp" ] || [ -x "/usr/local/bin/yt-dlp" ]; then
    echo "      yt-dlp già presente."
else
    curl -fL --progress-bar "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos" -o "$INSTALL_DIR/bin/yt-dlp"
    chmod +x "$INSTALL_DIR/bin/yt-dlp"
fi

if is_valid_bin "$INSTALL_DIR/bin/deno" || command -v deno >/dev/null 2>&1 || [ -x "/opt/homebrew/bin/deno" ] || [ -x "/usr/local/bin/deno" ]; then
    echo "      deno già presente."
else
    curl -fL --progress-bar "https://github.com/denoland/deno/releases/latest/download/deno-${DENO_ARCH}.zip" -o "$TMP_WORK/deno.zip"
    /usr/bin/unzip -oq "$TMP_WORK/deno.zip" -d "$INSTALL_DIR/bin"
    chmod +x "$INSTALL_DIR/bin/deno"
fi

echo "[5/6] Preparo ffmpeg, ffprobe e interfaccia nativa macOS..."
if is_valid_bin "$INSTALL_DIR/bin/ffmpeg" || command -v ffmpeg >/dev/null 2>&1 || [ -x "/opt/homebrew/bin/ffmpeg" ] || [ -x "/usr/local/bin/ffmpeg" ]; then
    echo "      ffmpeg già presente."
else
    curl -fL --progress-bar "https://github.com/eugeneware/ffmpeg-static/releases/download/${FFMPEG_RELEASE}/ffmpeg-${FFMPEG_ARCH}" -o "$INSTALL_DIR/bin/ffmpeg"
    chmod +x "$INSTALL_DIR/bin/ffmpeg"
fi

if is_valid_bin "$INSTALL_DIR/bin/ffprobe" || command -v ffprobe >/dev/null 2>&1 || [ -x "/opt/homebrew/bin/ffprobe" ] || [ -x "/usr/local/bin/ffprobe" ]; then
    echo "      ffprobe già presente."
else
    curl -fL --progress-bar "https://github.com/eugeneware/ffmpeg-static/releases/download/${FFMPEG_RELEASE}/ffprobe-${FFMPEG_ARCH}" -o "$INSTALL_DIR/bin/ffprobe"
    chmod +x "$INSTALL_DIR/bin/ffprobe"
fi

# Clean up any legacy JXA scripts from prior installs
rm -f "$INSTALL_DIR/src/slsk_setup.js" "$INSTALL_DIR/src/slsk_prompt.js"

if [ "$PREFER_SWIFT_UI" = "1" ] && xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null 2>&1; then
    echo "      Compilo finestra AppKit nativa (Swift, max 25s)..."
    INSTALL_DIR="$INSTALL_DIR" "$PYTHON_BIN" - << 'PYEOF' || true
import os, subprocess
from pathlib import Path
root = Path(os.environ["INSTALL_DIR"])
for src_name, bin_name in (("slsk_prompt.swift", "slsk-prompt"), ("slsk_setup.swift", "slsk-setup")):
    out_bin = root / "bin" / bin_name
    try:
        res = subprocess.run(
            ["swiftc", "-Onone", str(root / "src" / src_name), "-o", str(out_bin)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
        )
        if res.returncode == 0 and out_bin.is_file():
            out_bin.chmod(0o755)
        else:
            out_bin.unlink(missing_ok=True)
    except subprocess.TimeoutExpired:
        out_bin.unlink(missing_ok=True)
        print("      Compilazione Swift lenta: uso i dialog nativi macOS (istantanei, 0% CPU).")
        break
PYEOF
else
    rm -f "$INSTALL_DIR/bin/slsk-prompt" "$INSTALL_DIR/bin/slsk-setup"
    echo "      Profilo leggero attivo: uso i dialog nativi macOS (istantanei, 0% CPU, zero compilazione)."
fi

xattr -dr com.apple.quarantine "$INSTALL_DIR" 2>/dev/null || true

# Optional non-interactive seeding via env vars (used for CI/tests); normal users use the First-Run Finder window.
if [ -n "${SLSK_USER:-}" ] && [ -n "${SLSK_PASS:-}" ]; then
    INSTALL_DIR="$INSTALL_DIR" SLSK_USER="$SLSK_USER" SLSK_PASS="$SLSK_PASS" \
    DOWNLOAD_DIR="${SLSK_DOWNLOAD_DIR:-$HOME/Music/Soulseek Downloads}" \
    CONVERT_FMT="${SLSK_CONVERT_FORMAT:-${SLSK_DEFAULT_AIFF:-none}}" "$PYTHON_BIN" - << 'PYEOF'
import os, sys
from pathlib import Path
install_dir = Path(os.environ["INSTALL_DIR"])
sys.path.insert(0, str(install_dir / "src"))
from config import load_config, normalize_convert_format
from setup_ui import write_setup_configs
cfg = load_config(install_dir / "config" / "pipeline.json")
write_setup_configs(
    cfg,
    username=os.environ["SLSK_USER"],
    password=os.environ["SLSK_PASS"],
    output_dir=Path(os.environ["DOWNLOAD_DIR"]).expanduser(),
    convert_format=normalize_convert_format(os.environ["CONVERT_FMT"]),
)
PYEOF
fi

echo "[6/6] Installo il Servizio macOS (tasto destro -> Servizi -> Scarica con Soulseek)..."
INSTALL_DIR="$INSTALL_DIR" SLSK_SERVICES_DIR="$SERVICES_DIR" "$PYTHON_BIN" - << 'PYEOF'
import os
from pathlib import Path
import plistlib
import shlex

install_dir = os.environ["INSTALL_DIR"]
quoted_dir = shlex.quote(install_dir)
wf_contents = Path(os.environ["SLSK_SERVICES_DIR"]) / "Scarica con Soulseek.workflow" / "Contents"
wf_contents.mkdir(parents=True, exist_ok=True)

info_plist = {
    "NSServices": [
        {
            "NSBackgroundColorName": "background",
            "NSIconName": "NSActionTemplate",
            "NSMenuItem": {"default": "Scarica con Soulseek"},
            "NSMessage": "runWorkflowAsService",
            "NSSendTypes": ["public.utf8-plain-text"],
        }
    ]
}
with (wf_contents / "Info.plist").open("wb") as f:
    plistlib.dump(info_plist, f)

command_string = f'''PROJECT_DIR={quoted_dir}
export PATH="$PROJECT_DIR/bin:$PROJECT_DIR/runtime/python/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
SELECTED="$(cat)"

PYTHON_BIN=""
for p in "$PROJECT_DIR/runtime/python/bin/python3" "/opt/homebrew/bin/python3" "/usr/local/bin/python3" "/usr/bin/python3"; do
    if [ -x "$p" ]; then
        if [ "$p" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        PYTHON_BIN="$p"
        break
    fi
done

URL=$("$PYTHON_BIN" -c '
import sys
sys.path.insert(0, sys.argv[1])
from url_utils import extract_url_from_text, classify_url
raw = sys.stdin.read()
cand = extract_url_from_text(raw) or raw.strip()
try:
    print(classify_url(cand).url)
except Exception:
    pass
' "$PROJECT_DIR/src" <<< "$SELECTED")

if [ -z "$URL" ]; then
    /usr/bin/osascript -e 'display alert "Scarica con Soulseek" message "Nessun link YouTube o Spotify valido trovato nel testo selezionato." as warning' >/dev/null 2>&1
    exit 0
fi

RUNNER="/tmp/slsk_svc_$$.command"
QUOTED_URL=$("$PYTHON_BIN" -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$URL")
cat > "$RUNNER" << EOF
#!/bin/bash
rm -f -- "\\$0"
exec "$PROJECT_DIR/Soulseek Batch Pipeline.command" --pause $QUOTED_URL
EOF
chmod +x "$RUNNER"
/usr/bin/open -a Terminal "$RUNNER"'''

document_wflow = {
    "AMApplicationBuild": "534",
    "AMApplicationVersion": "2.10",
    "AMDocumentVersion": "2",
    "actions": [
        {
            "action": {
                "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
                "AMActionVersion": "2.0.3",
                "AMApplication": ["Automator"],
                "AMParameterProperties": {
                    "COMMAND_STRING": {},
                    "CheckedForUserDefaultShell": {},
                    "inputMethod": {},
                    "shell": {},
                    "source": {},
                },
                "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
                "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
                "ActionName": "Run Shell Script",
                "ActionParameters": {
                    "COMMAND_STRING": command_string,
                    "CheckedForUserDefaultShell": True,
                    "inputMethod": 0,
                    "shell": "/bin/zsh",
                    "source": "",
                },
                "BundleIdentifier": "com.apple.RunShellScript",
                "CFBundleVersion": "2.0.3",
                "CanShowSelectedItemsWhenRun": False,
                "CanShowWhenRun": True,
                "Category": ["AMCategoryUtilities"],
                "Class Name": "RunShellScriptAction",
                "InputUUID": "8D69B82C-4E12-4F55-9B24-112233445566",
                "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
                "OutputUUID": "A1B2C3D4-5E6F-4789-ABCD-998877665544",
                "UUID": "F1E2D3C4-B5A6-4987-8765-1234567890AB",
                "UnlocalizedApplications": ["Automator"],
                "arguments": {
                    "0": {"default value": 0, "name": "inputMethod", "required": "0", "type": "0", "uuid": "0"},
                    "1": {"default value": False, "name": "CheckedForUserDefaultShell", "required": "0", "type": "0", "uuid": "1"},
                    "2": {"default value": "", "name": "source", "required": "0", "type": "0", "uuid": "2"},
                    "3": {"default value": "", "name": "COMMAND_STRING", "required": "0", "type": "0", "uuid": "3"},
                    "4": {"default value": "/bin/sh", "name": "shell", "required": "0", "type": "0", "uuid": "4"},
                },
                "conversionLabel": 0,
                "isViewVisible": 1,
                "location": "309.000000:305.000000",
                "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
            },
            "isViewVisible": 1,
        }
    ],
    "connectors": {},
    "workflowMetaData": {
        "applicationBundleIDsByPath": {},
        "applicationPaths": [],
        "inputTypeIdentifier": "com.apple.Automator.text",
        "outputTypeIdentifier": "com.apple.Automator.nothing",
        "presentationMode": 11,
        "processesInput": False,
        "serviceInputTypeIdentifier": "com.apple.Automator.text",
        "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
        "serviceProcessesInput": False,
        "systemImageName": "NSActionTemplate",
        "useAutomaticInputType": False,
        "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
    },
}
with (wf_contents / "document.wflow").open("wb") as f:
    plistlib.dump(document_wflow, f)
PYEOF

/System/Library/CoreServices/pbs -update 2>/dev/null || true

# Launch First-Run Setup window once (Finder 'Sfoglia...' folder picker + Soulseek login).
if [ -z "${SLSK_SKIP_SETUP:-}" ] && [ ! -f "$INSTALL_DIR/config/sockseek.conf" ]; then
    echo ""
    echo "Apro la finestra di configurazione iniziale (credenziali Soulseek + cartella download)..."
    "$INSTALL_DIR/Soulseek Batch Pipeline.command" --setup || true
fi

SOCK_VER="$("$INSTALL_DIR/bin/sockseek" --version 2>/dev/null || echo "ok")"
echo ""
echo "=========================================="
echo "  Installazione completata! (sockseek $SOCK_VER)"
echo "=========================================="
echo "Cartella app: $INSTALL_DIR"
echo ""
echo "Come usarlo:"
echo "  1. Doppio click su 'Soulseek Batch Pipeline.command'"
echo "  2. Oppure seleziona un link Spotify o YouTube -> tasto destro -> Servizi -> Scarica con Soulseek"
echo "  (Per modificare credenziali o cartella in futuro: esegui con --setup)"
echo ""
if [ -z "${SLSK_NO_OPEN_FINDER:-}" ]; then
    /usr/bin/open "$INSTALL_DIR" 2>/dev/null || true
fi
