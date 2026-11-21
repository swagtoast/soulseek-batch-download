# Soulseek Batch Download (macOS, Linux & Windows — Zero Docker)

A lightweight, native pipeline for **macOS** (Apple Silicon & Intel), **Linux** (`x86_64` / `aarch64`, X11 & Wayland), and **Windows 10/11** to organize, convert, and tag albums, playlists, and tracks sourced from **Spotify** and **YouTube** links via **Soulseek** — with zero Docker, zero Homebrew/Winget, and zero administrator (`sudo`) privileges required.

> **Purpose & Fair-Use Notice**
>
> This project is **not** an invitation to pirate music. It is designed to simplify the creation and curation of a **hybrid personal music library**, where listeners who already own a **physical edition** of a cultural object (vinyl, CD, cassette, etc.) want a clean, lossless, accurately tagged **digital companion copy** for everyday listening, archiving, or DJing without having to manually rip and tag every disc by hand. Please support independent artists, musicians, and record labels by purchasing their physical releases and official digital editions.

---

## Quick Install (1 Command)

### macOS & Linux (Terminal — same command for both)
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/swagtoast/soulseek-batch-download/main/install.sh)
```

### Windows 10/11 (PowerShell)
```powershell
irm https://raw.githubusercontent.com/swagtoast/soulseek-batch-download/main/install.ps1 | iex
```

Each installer automatically profiles your machine (**OS, CPU architecture, core count, RAM, and desktop environment**) and installs only the missing standalone binaries (`sockseek`, `yt-dlp`, `deno`, static `ffmpeg` + `ffprobe`, and a standalone Python 3 runtime if needed):
- **macOS**: installs to `~/Applications/Soulseek Batch Download`, applies the custom app icon, compiles single-window Swift AppKit dialogs on Apple Silicon / high-spec Macs (or uses instant 0%-CPU native AppleScript dialogs on lighter Intel Macs), and registers the macOS **Right-Click → Services → Scarica con Soulseek / Download with Soulseek** workflow.
- **Linux**: installs to `~/.local/share/soulseek-batch-download` (zero `sudo`), adds the global `soulseek-batch` CLI command, desktop menu entry with icon, `soulseekdl://` URI handler, 3-tier native GUI (`tkinter` → `zenity` → `kdialog` → terminal), a lightweight X11/Wayland highlight-or-copy floating pill, and `browser-extension/` for pure browser right-click.
- **Windows 10/11**: installs to `%LOCALAPPDATA%\Soulseek Batch Download` (~465 MB on a clean PC, zero admin), creates Desktop & Start Menu shortcuts, registers `soulseekdl://`, starts the native WinForms/UI-Automation floating pill (highlight or copy any Spotify/YouTube link anywhere, or press `Ctrl+Alt+S`), and bundles `browser-extension/` for Chrome/Edge/Firefox right-click.

---

## Features

- **Native Bilingual Support (Italian & English)**: automatically detects your operating system language — if your system is set to **Italian**, the entire UI (native dialogs, terminal output, floating pill, browser extension, and missing-tracks report) switches to Italian; for every other system language it automatically uses **English** (can be overridden with `SLSK_LANG=it` or `SLSK_LANG=en`).
- **Auto-Update on Every Launch**: checks GitHub in ~0.15s via HTTP `ETag 304` (zero API quota usage, `2s` timeout) and updates code cleanly while preserving credentials, configs, downloads, and custom icons (skip with `--no-update`).
- **First-Run-Only Setup**: Soulseek credentials (saved locally with owner-only `600` permissions) and your download folder (chosen via native Finder / File Explorer / GTK / KDE **Browse…** picker) are requested only once (reopen anytime with `--setup`).
- **Single-Window Format Picker on Every Download**: choose between **`.flac (lossless)`**, **`.aiff (PCM 24-bit lossless)`**, or **`.mp3 (320 kbps)`**.
  - Selecting `.mp3` instructs `sockseek` to prefer 320 kbps MP3s (`--pref-format mp3 --pref-min-bitrate 320 --pref-max-bitrate 320`); any `.flac`, `.wav`, `.aiff`, `.m4a`, `.ogg`, or `.opus` returned by Soulseek is automatically converted to **MP3 320 kbps CBR** and the source file is removed after `ffprobe` verification.
  - Selecting `.aiff` converts `.flac`/`.wav` sources into **24-bit PCM AIFF** while leaving already-downloaded `.mp3` files untouched.
  - Selecting `.flac` converts uncompressed `.wav`/`.aiff` sources losslessly into **FLAC** and leaves `.flac` and `.mp3` untouched.
- **Spotify & YouTube / YouTube Music Extraction**:
  - **Spotify**: public playlists, full albums, and single tracks work out-of-the-box without an API key via the public Embed fallback (optional Spotify API credentials in `config/pipeline.json` unlock >100-track playlists).
  - **YouTube & YouTube Music**: playlists, album playlists (`OLAK5uy_...` / `browse/MPREb_...`), full-album videos (automatically inspects **chapters, video description, and top pinned/popular comments** to recover complete tracklists), and single videos.
  - Supports an optional local **MiniCPM5-2B-MLX** LoRA adapter on Apple Silicon (0 MB idle RAM), on-demand **Ollama**, and a zero-dependency built-in heuristic parser on all other machines.
- **Track-Count Verification, Missing-Track Rescue & Report**:
  - Verifies that every expected track in an album or playlist was actually downloaded; if any track is missing (even the very last track), automatically runs a targeted relaxed rescue pass (`--desperate --remove-ft --remove-brackets --length-tol -1`) into the same folder.
  - Keeps destination folders strictly clean (audio files only — automatically deletes `.nfo`, `.log`, `.cue`, `_index.csv`, loose cover files, and empty `Artwork/` folders), with **one intentional exception**: if any tracks still could not be found after the rescue pass, writes **`MISSING_TRACKS.txt`** (or **`BRANI_MANCANTI.txt`** in Italian) inside the destination folder listing the exact missing tracks.
- **Automatic Cross-Format Audio Tagger & Cover-Art Embedder (`.flac`, `.aiff`, `.mp3`, `.m4a`, `.wav`)**:
  - Audits every downloaded track via `ffprobe` for clean `artist`, `album`, `album_artist`, `title`, disc track order (`track_number/total_tracks`, `disc_number`), `date`, and embedded **cover art**.
  - Enriches missing or noisy tags via native Spotify metadata, **iTunes Search API**, and **MusicBrainz + Cover Art Archive** (zero LLM required on standard PCs — 0 MB extra footprint; optionally leverages a local fine-tuned MiniCPM5-2B-MLX Tagger LoRA on Apple Silicon when present).
  - For mixed playlists, tags each track with its **true original studio album** metadata and artwork, rejecting generic *"Greatest Hits"* / *"Now That's What I Call Music"* compilations.
  - Writes all tags and cover art **losslessly** via `ffmpeg -c:a copy` (zero audio re-encoding). Disable with `--no-tag`.
- **Automatic File Manager Reveal**: opens the downloaded album/playlist folder (or selects a single track) in **Finder** (macOS), **File Explorer** (Windows), or your **File Manager** (`xdg-open` / `nautilus` / `dolphin` on Linux) when finished.

---

## Usage

1. **Quick Trigger (Highlight / Right-Click)**:
   - **macOS**: highlight any Spotify or YouTube link → **Right-Click → Services → Scarica con Soulseek**.
   - **Windows & Linux**: highlight or copy any Spotify or YouTube link → click the floating **`Download with Soulseek`** pill (or load `browser-extension/` in Chrome / Brave / Edge / Firefox for pure right-click context menu support via `soulseekdl://`).
2. **Launcher Icon**:
   - **macOS**: double-click `Soulseek Batch Pipeline.command` inside `~/Applications/Soulseek Batch Download`.
   - **Linux**: open **Soulseek Batch Download** from your app menu, or run `soulseek-batch`.
   - **Windows**: double-click **Soulseek Batch Download** on your Desktop or Start Menu.
3. **Command Line**:
   ```bash
   # macOS
   "$HOME/Applications/Soulseek Batch Download/Soulseek Batch Pipeline.command" "URL"

   # Linux
   soulseek-batch "URL"

   # Windows (PowerShell)
   & "$env:LOCALAPPDATA\Soulseek Batch Download\Soulseek Batch Pipeline.bat" "URL"
   ```
   Useful flags: `--setup`, `--export-only`, `--mode auto|album|tracks`, `--convert flac|aiff|mp3` (`--convert-aiff`, `--convert-mp3`, `--no-convert`), `--no-tag`, `--no-finder`, `--no-gui`, `--update`, `--no-update`.
