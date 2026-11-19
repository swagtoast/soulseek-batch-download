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

from config import PipelineConfig, load_config, save_convert_preference
from downloader import download_collection
from models import CollectionExport
from setup_ui import ensure_configured
from spotify_extractor import extract_spotify
from url_utils import classify_url
from youtube_extractor import extract_youtube


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
    return sys.platform == "darwin" and cfg.prompt_js.is_file() and Path("/usr/bin/osascript").is_file()


def _prompt_with_gui(
    cfg: PipelineConfig,
    url: str | None,
    default_convert: bool,
) -> tuple[bool, str, bool] | None:
    extra_args = ["--default-convert", "true" if default_convert else "false"]
    if url:
        extra_args.extend(["--url", url])

    if cfg.prompt_bin.is_file() and os.access(cfg.prompt_bin, os.X_OK):
        cmd = [str(cfg.prompt_bin), *extra_args]
    else:
        cmd = ["/usr/bin/osascript", "-l", "JavaScript", str(cfg.prompt_js), *extra_args]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    raw = (proc.stdout or "").strip()
    if not raw:
        return None if proc.returncode != 0 else (False, "", default_convert)
    try:
        data = json.loads(raw)
        return (
            bool(data.get("confirmed")),
            str(data.get("url") or ""),
            bool(data.get("convert_flac_to_aiff", default_convert)),
        )
    except json.JSONDecodeError:
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
        "--convert-aiff",
        dest="convert_aiff",
        action="store_true",
        default=None,
        help="Converte i .flac scaricati in .aiff (ed elimina i .flac), lasciando gli .mp3 invariati",
    )
    parser.add_argument(
        "--no-convert-aiff",
        dest="convert_aiff",
        action="store_false",
        help="Mantiene i .flac originali senza convertirli in .aiff",
    )
    parser.add_argument("--no-gui", action="store_true", help="Disabilita finestre grafiche di input/setup")
    parser.add_argument("--no-finder", action="store_true", help="Non aprire il Finder al termine del download")
    parser.add_argument("--pause", action="store_true", help="Attendi Invio alla fine (utile da .command)")
    args = parser.parse_args(argv)

    cfg = load_config()

    # First-run-only setup (Soulseek credentials + Finder "Sfoglia..." folder picker).
    if args.setup or not args.export_only:
        updated_cfg = ensure_configured(cfg, force_setup=args.setup, no_gui=args.no_gui)
        if updated_cfg is None:
            print("Configurazione iniziale annullata.")
            return 0
        cfg = updated_cfg
        if args.setup and not args.url:
            return 0

    raw_input = args.url
    convert_aiff = cfg.convert_flac_to_aiff if args.convert_aiff is None else bool(args.convert_aiff)
    interactive = False

    if not raw_input:
        interactive = True
        gui_res = None
        if _can_use_gui_prompt(cfg, args.no_gui) and not args.export_only:
            gui_res = _prompt_with_gui(cfg, None, convert_aiff)
        if gui_res is not None:
            confirmed, chosen_url, chosen_convert = gui_res
            if not confirmed:
                print("Operazione annullata.")
                return 0
            raw_input = chosen_url
            if args.convert_aiff is None:
                convert_aiff = chosen_convert
                save_convert_preference(cfg, convert_aiff)
        else:
            try:
                raw_input = input("Incolla URL Spotify o YouTube (playlist / album / video full album): ").strip()
            except EOFError:
                return 1
    elif args.convert_aiff is not None:
        save_convert_preference(cfg, convert_aiff)

    try:
        export = extract_collection(raw_input, cfg)
        print_summary(export)
        rc = download_collection(
            export,
            cfg,
            export_only=args.export_only,
            force_mode=args.mode,
            convert_aiff=convert_aiff,
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
