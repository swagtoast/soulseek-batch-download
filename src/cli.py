"""Unified CLI entry point for Soulseek Batch Pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse

# Ensure sibling modules under src/ resolve cleanly regardless of cwd.
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import PipelineConfig, load_config, normalize_convert_format, save_convert_preference
from downloader import download_collection
from i18n import t
from models import CollectionExport
from setup_ui import ensure_configured, is_first_run_needed
from spotify_extractor import extract_spotify
from updater import check_and_apply_update
from url_utils import classify_url
from youtube_extractor import extract_youtube


_KIND_LABEL_KEYS = {
    "spotify_playlist": "kind_spotify_playlist",
    "spotify_album": "kind_spotify_album",
    "spotify_track": "kind_spotify_track",
    "youtube_playlist": "kind_youtube_playlist",
    "youtube_video": "kind_youtube_video",
}


def _describe_source_url(raw_input: str) -> str:
    try:
        parsed = classify_url(raw_input)
        return t(_KIND_LABEL_KEYS.get(parsed.kind, "kind_generic"))
    except Exception:
        return t("kind_generic")


def extract_collection(raw_input: str, cfg: PipelineConfig) -> CollectionExport:
    """Classify URL and dispatch to Spotify or YouTube extractor."""
    parsed = classify_url(raw_input)
    print(t("cli_detected", kind=parsed.kind, url=parsed.url))

    if parsed.kind.startswith("spotify_"):
        return extract_spotify(parsed, cfg.spotify_client_id, cfg.spotify_client_secret)
    if parsed.kind.startswith("youtube_"):
        return extract_youtube(parsed, cfg)
    raise ValueError(t("cli_unsupported", kind=parsed.kind))


def print_summary(export: CollectionExport) -> None:
    """Display extracted metadata and track preview."""
    kind_label = {
        "playlist": t("summary_playlist"),
        "album": t("summary_album"),
        "single_track": t("summary_single_track"),
    }[export.kind]
    print(f"\n=== {kind_label}: {export.name} ===")
    if export.kind == "album" and export.artist:
        print(t("summary_artist_album", artist=export.artist, album=export.album))
    llm_note = t("summary_formatted_llm") if export.used_llm else ""
    print(t("summary_tracks_extracted", count=len(export.tracks)) + llm_note)
    for idx, tr in enumerate(export.tracks[:15], start=1):
        dur = f" [{tr.duration_sec}s]" if tr.duration_sec else ""
        prefix = f"{tr.artist} - " if tr.artist else ""
        print(f"  {idx:02d}. {prefix}{tr.title}{dur}")
    if len(export.tracks) > 15:
        print(t("summary_more_tracks", count=len(export.tracks) - 15))
    print("")


def _can_use_gui_prompt(cfg: PipelineConfig, no_gui: bool) -> bool:
    if no_gui or os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if os.name == "nt":
        return cfg.win_prompt_ps1.is_file()
    if sys.platform.startswith("linux"):
        try:
            from linux.slsk_ui import has_linux_gui_backend
            return has_linux_gui_backend()
        except Exception:
            return False
    if cfg.prompt_bin.is_file() and os.access(cfg.prompt_bin, os.X_OK):
        return True
    return sys.platform == "darwin" and Path("/usr/bin/osascript").is_file()


def _unwrap_protocol_url(raw: str | None) -> str | None:
    if not raw:
        return raw
    s = raw.strip()
    if s.lower().startswith("soulseekdl://"):
        inner = s[len("soulseekdl://") :].lstrip("/")
        return urllib.parse.unquote(inner).strip()
    return s


def _prompt_with_windows(
    cfg: PipelineConfig,
    url: str | None,
    default_format: str,
    label: str | None = None,
) -> tuple[bool, str, str] | None:
    if not cfg.win_prompt_ps1.is_file():
        return None
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-STA",
        "-File",
        str(cfg.win_prompt_ps1),
        "-DefaultFormat",
        normalize_convert_format(default_format),
    ]
    if url:
        cmd.extend(["-Url", url])
    if label:
        cmd.extend(["-Label", label])
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    raw = (proc.stdout or "").strip()
    if not raw:
        return None if proc.returncode != 0 else (False, "", default_format)
    try:
        data = json.loads(raw.splitlines()[-1])
        raw_fmt = data.get("convert_format", default_format)
        return (
            bool(data.get("confirmed")),
            str(data.get("url") or ""),
            normalize_convert_format(raw_fmt),
        )
    except json.JSONDecodeError:
        return None


def _as_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _run_osascript(script: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "").strip()


def _clipboard_music_url() -> str:
    try:
        proc = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, text=True, check=False)
        clip = (proc.stdout or "").strip()
        lower = clip.lower()
        if any(tok in lower for tok in ("spotify.com/", "spotify.link/", "spoti.fi/", "spotify:", "youtube.com/", "youtu.be/")):
            return clip
    except OSError:
        pass
    return ""


def _prompt_with_applescript(
    url: str | None,
    default_format: str,
    label: str | None = None,
) -> tuple[bool, str, str]:
    """Universal zero-CPU StandardAdditions macOS single-window format picker (all Intel & Apple Silicon Macs)."""
    final_url = (url or "").strip()
    btn_cancel = _as_escape(t("ui_btn_cancel"))
    btn_next = _as_escape(t("ui_btn_next"))
    btn_dl = _as_escape(t("ui_btn_download"))
    if not final_url:
        clip = _as_escape(_clipboard_music_url())
        paste_msg = _as_escape(t("cli_paste_dialog"))
        rc, entered = _run_osascript(
            f'set r to display dialog "{paste_msg}" '
            f'with title "Soulseek Batch Download" default answer "{clip}" '
            f'buttons {{"{btn_cancel}", "{btn_next}"}} default button "{btn_next}"\n'
            f'return text returned of r'
        )
        if rc != 0 or not entered.strip():
            return (False, "", default_format)
        final_url = entered.strip()

    items = [
        ".flac (lossless)",
        ".aiff (PCM 24-bit lossless)",
        ".mp3 (320 kbps)",
    ]
    fmt_map = {items[0]: "flac", items[1]: "aiff", items[2]: "mp3"}
    default_item = {"flac": items[0], "aiff": items[1], "mp3": items[2]}.get(
        normalize_convert_format(default_format), items[0]
    )
    fmt_lbl = _as_escape(t("ui_format_label"))
    heading = _as_escape(label or fmt_lbl)
    prompt_text = f"{heading} — {fmt_lbl}" if label else fmt_lbl
    rc, chosen_label = _run_osascript(
        'set opts to {".flac (lossless)", ".aiff (PCM 24-bit lossless)", ".mp3 (320 kbps)"}\n'
        f'set c to choose from list opts with title "Soulseek Batch Download" '
        f'with prompt "{prompt_text}" default items {{"{default_item}"}} '
        f'OK button name "{btn_dl}" cancel button name "{btn_cancel}"\n'
        'if c is false then error number -128\n'
        'return item 1 of c'
    )
    if rc != 0 or not chosen_label:
        return (False, final_url, default_format)
    return (True, final_url, fmt_map.get(chosen_label, "flac"))


def _prompt_with_gui(
    cfg: PipelineConfig,
    url: str | None,
    default_format: str,
    label: str | None = None,
) -> tuple[bool, str, str] | None:
    if os.name == "nt":
        return _prompt_with_windows(cfg, url, default_format, label=label)

    if sys.platform.startswith("linux"):
        try:
            from linux.slsk_ui import run_linux_prompt
            return run_linux_prompt(url, default_format, label=label)
        except Exception:
            return None

    if cfg.prompt_bin.is_file() and os.access(cfg.prompt_bin, os.X_OK):
        extra_args = ["--default-format", normalize_convert_format(default_format)]
        if url:
            extra_args.extend(["--url", url])
        if label:
            extra_args.extend(["--label", label])

        proc = subprocess.run([str(cfg.prompt_bin), *extra_args], capture_output=True, text=True, check=False)
        raw = (proc.stdout or "").strip()
        if not raw:
            return None if proc.returncode != 0 else (False, "", default_format)
        try:
            data = json.loads(raw)
            raw_fmt = data.get("convert_format", data.get("convert_flac_to_aiff", default_format))
            return (
                bool(data.get("confirmed")),
                str(data.get("url") or ""),
                normalize_convert_format(raw_fmt),
            )
        except json.JSONDecodeError:
            return None

    if sys.platform == "darwin" and Path("/usr/bin/osascript").is_file():
        return _prompt_with_applescript(url, default_format, label=label)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="soulseek-pipeline",
        description="Esporta playlist/album da Spotify o YouTube e scarica da Soulseek senza Docker.",
    )
    parser.add_argument("url", nargs="?", help="URL Spotify o YouTube (playlist, album o video full album)")
    parser.add_argument("--setup", action="store_true", help="Apri la finestra di configurazione iniziale")
    parser.add_argument("--export-only", action="store_true", help="Esporta solo il file CSV senza avviare Soulseek")
    parser.add_argument(
        "--mode",
        choices=("auto", "album", "tracks"),
        default="auto",
        help="Forza modalità Full Album o traccia-per-traccia (default: auto)",
    )
    parser.add_argument(
        "--convert",
        dest="convert_format",
        choices=("flac", "none", "aiff", "mp3"),
        default=None,
        help="Formato desiderato: flac (nessuna conversione), aiff (PCM 24-bit) o mp3 (320 kbps)",
    )
    parser.add_argument(
        "--convert-aiff",
        dest="convert_format",
        action="store_const",
        const="aiff",
        help="Scorciatoia per --convert aiff (FLAC -> AIFF 24-bit)",
    )
    parser.add_argument(
        "--convert-mp3",
        dest="convert_format",
        action="store_const",
        const="mp3",
        help="Scorciatoia per --convert mp3 (FLAC -> MP3 320 kbps)",
    )
    parser.add_argument(
        "--no-convert",
        "--no-convert-aiff",
        dest="convert_format",
        action="store_const",
        const="flac",
        help="Mantiene i .flac originali senza convertirli",
    )
    parser.add_argument(
        "--delete-original",
        dest="delete_original",
        action="store_true",
        default=None,
        help="Elimina il file .flac originale dopo la conversione verificata",
    )
    parser.add_argument(
        "--keep-original",
        dest="delete_original",
        action="store_false",
        help="Mantiene il file .flac originale dopo la conversione",
    )
    parser.add_argument("--no-gui", action="store_true", help="Disabilita finestre grafiche di input/setup")
    parser.add_argument("--no-finder", action="store_true", help="Non aprire il Finder al termine del download")
    parser.add_argument("--no-tag", action="store_true", help="Salta verifica e correzione automatica dei metadati/copertine")
    parser.add_argument("--update", action="store_true", help="Controlla e installa subito eventuali aggiornamenti da GitHub")
    parser.add_argument("--no-update", action="store_true", help="Salta il controllo automatico aggiornamenti")
    parser.add_argument("--pause", action="store_true", help="Attendi Invio alla fine (utile da .command)")
    args = parser.parse_args(argv)

    cfg = load_config()

    # Lightweight auto-update check (at most once every 6h; skipped on local git checkouts unless --update).
    if args.update or (not args.no_update and not args.export_only):
        updated = check_and_apply_update(cfg, force=args.update)
        if updated:
            if args.update and not args.url and not args.setup:
                return 0
            # Re-exec transparently with the freshly updated code (only when running as main CLI).
            if argv is None:
                os.environ["SLSK_JUST_UPDATED"] = "1"
                restart_args = [a for a in sys.argv[1:] if a != "--update"]
                if os.name == "nt":
                    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *restart_args], check=False)
                    return proc.returncode
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *restart_args])
        elif args.update and not args.url and not args.setup:
            return 0

    did_first_run_setup = False

    # First-run-only setup (Soulseek credentials + Finder/Explorer "Sfoglia..." folder picker).
    if args.setup or not args.export_only:
        was_unconfigured = is_first_run_needed(cfg)
        updated_cfg = ensure_configured(cfg, force_setup=args.setup, no_gui=args.no_gui)
        if updated_cfg is None:
            print(t("cli_setup_cancelled"))
            return 0
        cfg = updated_cfg
        did_first_run_setup = was_unconfigured or args.setup
        if args.setup and not args.url:
            return 0

    raw_input = _unwrap_protocol_url(args.url)
    convert_format = cfg.convert_format if args.convert_format is None else normalize_convert_format(args.convert_format)
    interactive = False

    if not raw_input:
        interactive = True
        gui_res = None
        if _can_use_gui_prompt(cfg, args.no_gui) and not args.export_only:
            gui_res = _prompt_with_gui(cfg, None, convert_format)
        if gui_res is not None:
            confirmed, chosen_url, chosen_convert = gui_res
            if not confirmed:
                print(t("cli_cancelled"))
                return 0
            raw_input = chosen_url
            if args.convert_format is None:
                convert_format = chosen_convert
            save_convert_preference(cfg, convert_format, True)
        else:
            try:
                raw_input = input(t("cli_paste_prompt")).strip()
            except EOFError:
                return 1
    elif args.convert_format is not None:
        save_convert_preference(cfg, convert_format, True)
    elif not args.export_only and not did_first_run_setup and _can_use_gui_prompt(cfg, args.no_gui):
        # Single-window format selector (.flac / .aiff / .mp3) for every download
        # (Spotify or YouTube, playlist or album or track) unless explicit CLI flags or --no-gui were passed.
        gui_res = _prompt_with_gui(
            cfg,
            raw_input,
            convert_format,
            label=_describe_source_url(raw_input),
        )
        if gui_res is not None:
            confirmed, chosen_url, chosen_convert = gui_res
            if not confirmed:
                print(t("cli_cancelled"))
                return 0
            raw_input = chosen_url or raw_input
            convert_format = chosen_convert
            save_convert_preference(cfg, convert_format, True)

    try:
        export = extract_collection(raw_input, cfg)
        print_summary(export)
        rc = download_collection(
            export,
            cfg,
            export_only=args.export_only,
            force_mode=args.mode,
            convert_format=convert_format,
            delete_original=True,
            open_finder=not args.no_finder,
            auto_tag=not args.no_tag,
        )
    except KeyboardInterrupt:
        print(t("cli_interrupted"))
        rc = 130
    except Exception as exc:
        print(t("cli_error", exc=exc))
        rc = 1

    if args.pause or interactive:
        try:
            input(t("cli_press_enter"))
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
