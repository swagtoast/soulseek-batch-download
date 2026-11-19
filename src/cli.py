"""Unified CLI entry point for Soulseek Batch Pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

# Ensure sibling modules under src/ resolve cleanly regardless of cwd.
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import PipelineConfig, load_config, normalize_convert_format, save_convert_preference
from downloader import download_collection
from models import CollectionExport
from setup_ui import ensure_configured, is_first_run_needed
from spotify_extractor import extract_spotify
from updater import check_and_apply_update
from url_utils import classify_url
from youtube_extractor import extract_youtube


_KIND_LABELS = {
    "spotify_playlist": "Playlist Spotify rilevata",
    "spotify_album": "Full Album Spotify rilevato",
    "spotify_track": "Brano Spotify rilevato",
    "youtube_playlist": "Playlist / Album YouTube rilevato",
    "youtube_video": "Video / Full Album YouTube rilevato",
}


def _describe_source_url(raw_input: str) -> str:
    try:
        parsed = classify_url(raw_input)
        return _KIND_LABELS.get(parsed.kind, "Link rilevato")
    except Exception:
        return "Link rilevato"


def extract_collection(raw_input: str, cfg: PipelineConfig) -> CollectionExport:
    """Classify URL and dispatch to Spotify or YouTube extractor."""
    parsed = classify_url(raw_input)
    print(f"[Input] Rilevato: {parsed.kind} -> {parsed.url}")

    if parsed.kind.startswith("spotify_"):
        return extract_spotify(parsed, cfg.spotify_client_id, cfg.spotify_client_secret)
    if parsed.kind.startswith("youtube_"):
        return extract_youtube(parsed, cfg)
    raise ValueError(f"Tipo sorgente non gestito: {parsed.kind}")


def print_summary(export: CollectionExport) -> None:
    """Display extracted metadata and track preview."""
    kind_label = {
        "playlist": "Playlist",
        "album": "Full Album",
        "single_track": "Brano singolo",
    }[export.kind]
    print(f"\n=== {kind_label}: {export.name} ===")
    if export.kind == "album" and export.artist:
        print(f"Artista: {export.artist} | Album: {export.album}")
    print(f"Tracce estratte: {len(export.tracks)}" + (" (formattate con LLM)" if export.used_llm else ""))
    for idx, tr in enumerate(export.tracks[:15], start=1):
        dur = f" [{tr.duration_sec}s]" if tr.duration_sec else ""
        prefix = f"{tr.artist} - " if tr.artist else ""
        print(f"  {idx:02d}. {prefix}{tr.title}{dur}")
    if len(export.tracks) > 15:
        print(f"  ... e altre {len(export.tracks) - 15} tracce")
    print("")


def _can_use_gui_prompt(cfg: PipelineConfig, no_gui: bool) -> bool:
    if no_gui or os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if cfg.prompt_bin.is_file() and os.access(cfg.prompt_bin, os.X_OK):
        return True
    return sys.platform == "darwin" and Path("/usr/bin/osascript").is_file()


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
    default_delete: bool = True,
    label: str | None = None,
) -> tuple[bool, str, str, bool]:
    """Universal zero-CPU StandardAdditions macOS dialogs (works on all Intel & Apple Silicon Macs)."""
    final_url = (url or "").strip()
    if not final_url:
        clip = _as_escape(_clipboard_music_url())
        rc, entered = _run_osascript(
            f'set r to display dialog "Incolla un URL Spotify o YouTube (playlist, album o video full album):" '
            f'with title "Soulseek Batch Download" default answer "{clip}" '
            f'buttons {{"Annulla", "Avanti"}} default button "Avanti"\n'
            f'return text returned of r'
        )
        if rc != 0 or not entered.strip():
            return (False, "", default_format, default_delete)
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
    heading = _as_escape(label or "formato")
    prompt_text = f"{heading} — formato" if label else "formato"
    rc, chosen_label = _run_osascript(
        'set opts to {".flac (lossless)", ".aiff (PCM 24-bit lossless)", ".mp3 (320 kbps)"}\n'
        f'set c to choose from list opts with title "Soulseek Batch Download" '
        f'with prompt "{prompt_text}" default items {{"{default_item}"}} '
        f'OK button name "Scarica" cancel button name "Annulla"\n'
        'if c is false then error number -128\n'
        'return item 1 of c'
    )
    if rc != 0 or not chosen_label:
        return (False, final_url, default_format, default_delete)
    chosen_fmt = fmt_map.get(chosen_label, "flac")

    chosen_delete = default_delete
    if chosen_fmt in {"aiff", "mp3"}:
        def_btn = "Elimina file originale" if default_delete else "Mantieni originale"
        rc, btn = _run_osascript(
            'set r to display dialog "Eliminare il file .flac originale dopo la conversione?" '
            'with title "Soulseek Batch Download" '
            f'buttons {{"Annulla", "Mantieni originale", "Elimina file originale"}} '
            f'default button "{def_btn}"\n'
            'return button returned of r'
        )
        if rc != 0:
            return (False, final_url, chosen_fmt, default_delete)
        chosen_delete = btn == "Elimina file originale"

    return (True, final_url, chosen_fmt, chosen_delete)


def _prompt_with_gui(
    cfg: PipelineConfig,
    url: str | None,
    default_format: str,
    default_delete: bool = True,
    label: str | None = None,
) -> tuple[bool, str, str, bool] | None:
    if cfg.prompt_bin.is_file() and os.access(cfg.prompt_bin, os.X_OK):
        extra_args = [
            "--default-format",
            normalize_convert_format(default_format),
            "--default-delete",
            "true" if default_delete else "false",
        ]
        if url:
            extra_args.extend(["--url", url])
        if label:
            extra_args.extend(["--label", label])

        proc = subprocess.run([str(cfg.prompt_bin), *extra_args], capture_output=True, text=True, check=False)
        raw = (proc.stdout or "").strip()
        if not raw:
            return None if proc.returncode != 0 else (False, "", default_format, default_delete)
        try:
            data = json.loads(raw)
            raw_fmt = data.get("convert_format", data.get("convert_flac_to_aiff", default_format))
            return (
                bool(data.get("confirmed")),
                str(data.get("url") or ""),
                normalize_convert_format(raw_fmt),
                bool(data.get("delete_original", default_delete)),
            )
        except json.JSONDecodeError:
            return None

    if sys.platform == "darwin" and Path("/usr/bin/osascript").is_file():
        return _prompt_with_applescript(url, default_format, default_delete=default_delete, label=label)
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
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *restart_args])
        elif args.update and not args.url and not args.setup:
            return 0

    did_first_run_setup = False

    # First-run-only setup (Soulseek credentials + Finder "Sfoglia..." folder picker).
    if args.setup or not args.export_only:
        was_unconfigured = is_first_run_needed(cfg)
        updated_cfg = ensure_configured(cfg, force_setup=args.setup, no_gui=args.no_gui)
        if updated_cfg is None:
            print("Configurazione iniziale annullata.")
            return 0
        cfg = updated_cfg
        did_first_run_setup = was_unconfigured or args.setup
        if args.setup and not args.url:
            return 0

    raw_input = args.url
    convert_format = cfg.convert_format if args.convert_format is None else normalize_convert_format(args.convert_format)
    delete_original = cfg.delete_original if args.delete_original is None else bool(args.delete_original)
    interactive = False

    if not raw_input:
        interactive = True
        gui_res = None
        if _can_use_gui_prompt(cfg, args.no_gui) and not args.export_only:
            gui_res = _prompt_with_gui(cfg, None, convert_format, default_delete=delete_original)
        if gui_res is not None:
            confirmed, chosen_url, chosen_convert, chosen_delete = gui_res
            if not confirmed:
                print("Operazione annullata.")
                return 0
            raw_input = chosen_url
            if args.convert_format is None:
                convert_format = chosen_convert
            if args.delete_original is None:
                delete_original = chosen_delete
            save_convert_preference(cfg, convert_format, delete_original)
        else:
            try:
                raw_input = input("Incolla URL Spotify o YouTube (playlist / album / video full album): ").strip()
            except EOFError:
                return 1
    elif args.convert_format is not None or args.delete_original is not None:
        save_convert_preference(cfg, convert_format, delete_original)
    elif not args.export_only and not did_first_run_setup and _can_use_gui_prompt(cfg, args.no_gui):
        # Show format selector (.flac / .aiff / .mp3) + delete_original checkbox for every download
        # (Spotify or YouTube, playlist or album or track) unless explicit CLI flags or --no-gui were passed.
        gui_res = _prompt_with_gui(
            cfg,
            raw_input,
            convert_format,
            default_delete=delete_original,
            label=_describe_source_url(raw_input),
        )
        if gui_res is not None:
            confirmed, chosen_url, chosen_convert, chosen_delete = gui_res
            if not confirmed:
                print("Operazione annullata.")
                return 0
            raw_input = chosen_url or raw_input
            convert_format = chosen_convert
            delete_original = chosen_delete
            save_convert_preference(cfg, convert_format, delete_original)

    try:
        export = extract_collection(raw_input, cfg)
        print_summary(export)
        rc = download_collection(
            export,
            cfg,
            export_only=args.export_only,
            force_mode=args.mode,
            convert_format=convert_format,
            delete_original=delete_original,
            open_finder=not args.no_finder,
        )
    except KeyboardInterrupt:
        print("\nInterrotto dall'utente.")
        rc = 130
    except Exception as exc:
        print(f"\n[Errore] {exc}")
        rc = 1

    if args.pause or interactive:
        try:
            input("\nPremi Invio per chiudere...")
        except EOFError:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
