"""Sockseek download runner supporting playlists, single tracks, full-album fallback, and FLAC->AIFF."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys

from config import PipelineConfig, normalize_convert_format
from converter import convert_downloaded_flacs
from models import CollectionExport


AUDIO_EXTENSIONS = {".flac", ".mp3", ".aiff", ".aif", ".wav", ".m4a", ".ogg", ".opus", ".alac"}


def write_tracks_csv(export: CollectionExport, csv_path: Path) -> Path:
    """Write tracks in RFC 4180 CSV format compatible with Sockseek."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    include_length = any(t.duration_sec is not None and t.duration_sec > 0 for t in export.tracks)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if include_length:
            writer.writerow(["Artist", "Title", "Album", "Length"])
        else:
            writer.writerow(["Artist", "Title", "Album"])

        for track in export.tracks:
            row = [track.artist, track.title, track.album]
            if include_length:
                row.append(str(track.duration_sec) if track.duration_sec and track.duration_sec > 0 else "")
            writer.writerow(row)
    return csv_path


def _snapshot_files(root: Path) -> dict[Path, float]:
    """Return a map of existing file paths to mtimes under output_dir."""
    if not root.is_dir():
        return {}
    snap: dict[Path, float] = {}
    for p in root.rglob("*"):
        if p.is_file():
            try:
                snap[p] = p.stat().st_mtime
            except OSError:
                pass
    return snap


def _new_audio_files(before: dict[Path, float], root: Path) -> list[Path]:
    after = _snapshot_files(root)
    result: list[Path] = []
    for path, mtime in after.items():
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if path not in before or mtime > before[path] + 0.001:
            result.append(path)
    return sorted(result)


def _cleanup_non_audio_files(before_files: dict[Path, float], before_dirs: set[Path], root: Path) -> int:
    """Immediately delete any non-audio files (covers, nfo, txt, cue, log, csv, etc.) downloaded in this session."""
    if not root.is_dir():
        return 0
    removed = 0
    for path in list(root.rglob("*")):
        if not path.is_file():
            continue
        if path in before_files:
            continue
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass

    # Remove any newly created subdirectories that are now empty (e.g. Artwork/, Scans/).
    dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        if d in before_dirs:
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    if removed:
        print(f"[Pulizia] Rimossi {removed} file non audio.")
    return removed


def _prune_empty_new_dirs(before_dirs: set[Path], root: Path) -> None:
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir() and entry not in before_dirs:
            try:
                if not any(entry.rglob("*")):
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass


def _run_sockseek(args: list[str]) -> int:
    proc = subprocess.run(args, check=False)
    return proc.returncode


def _build_album_query(export: CollectionExport) -> str:
    if export.artist and export.album:
        if " - " in export.artist and "," not in export.artist and "," not in export.album:
            return f"artist={export.artist}, album={export.album}"
        return f"{export.artist} - {export.album}"
    return export.album or export.name


def _platform_extra_args(export: CollectionExport) -> list[str]:
    if export.source_platform == "youtube":
        # YouTube uploads/vinyl rips frequently differ by >5s from Soulseek lossless releases.
        return ["--length-tol", "15", "--pref-length-tol", "8"]
    return []


def _format_search_args(convert_format: str) -> list[str]:
    """Tell Sockseek to prefer lightweight MP3 320 kbps when the user chose .mp3, or lossless otherwise."""
    fmt = normalize_convert_format(convert_format)
    if fmt == "mp3":
        return [
            "--pref-format",
            "mp3",
            "--pref-min-bitrate",
            "320",
            "--format",
            "mp3,flac,aiff,wav",
        ]
    if fmt == "aiff":
        return [
            "--pref-format",
            "aiff,flac,wav",
            "--pref-min-bitrate",
            "320",
            "--format",
            "flac,aiff,wav,mp3",
        ]
    return [
        "--pref-format",
        "flac,aiff,wav",
        "--pref-min-bitrate",
        "320",
        "--format",
        "flac,aiff,wav,mp3",
    ]


def _maybe_convert(
    cfg: PipelineConfig,
    convert_format: str,
    before_files: dict[Path, float],
) -> None:
    fmt = normalize_convert_format(convert_format)
    if fmt == "flac":
        return
    new_audio = _new_audio_files(before_files, cfg.output_dir)
    if not new_audio:
        return
    convert_downloaded_flacs(
        downloaded_audio=new_audio,
        target_format=fmt,
        ffmpeg_bin=cfg.ffmpeg_bin,
        ffprobe_bin=cfg.ffprobe_bin,
        delete_original=True,
    )


def _open_in_finder(target: Path, reveal: bool = False) -> None:
    """Open a downloaded folder (or reveal a single file) in macOS Finder."""
    if sys.platform != "darwin" or not target.exists():
        return
    cmd = ["/usr/bin/open", "-R", str(target)] if reveal else ["/usr/bin/open", str(target)]
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _reveal_after_download(
    cfg: PipelineConfig,
    before_files: dict[Path, float],
    preferred_dir: Path | None,
) -> None:
    post_audio = _new_audio_files(before_files, cfg.output_dir)
    if len(post_audio) == 1 and post_audio[0].parent == cfg.output_dir:
        print(f"[Finder] Apro nel Finder: {post_audio[0]}")
        _open_in_finder(post_audio[0], reveal=True)
        return

    target_dir: Path | None = None
    if post_audio:
        common = Path(os.path.commonpath([str(p.parent) for p in post_audio]))
        if common != cfg.output_dir and common.is_dir():
            target_dir = common
        elif preferred_dir and preferred_dir.is_dir():
            target_dir = preferred_dir
        elif common.is_dir():
            target_dir = common
    elif preferred_dir and preferred_dir.is_dir():
        target_dir = preferred_dir

    if target_dir is not None:
        print(f"[Finder] Apro cartella nel Finder: {target_dir}")
        _open_in_finder(target_dir, reveal=False)


def _execute_download(
    export: CollectionExport,
    effective_kind: str,
    csv_path: Path,
    cfg: PipelineConfig,
    session_before_files: dict[Path, float],
    convert_format: str = "flac",
) -> tuple[int, Path | None]:
    extra_args = [
        "--no-write-index",
        *_format_search_args(convert_format),
        *_platform_extra_args(export),
    ]
    base_cmd = [
        str(cfg.sockseek_bin),
        "-c",
        str(cfg.sockseek_conf),
        "-o",
        str(cfg.output_dir),
        *extra_args,
    ]

    if effective_kind == "single_track" and export.tracks:
        t = export.tracks[0]
        if not t.artist:
            query = t.title if "," in t.title else f"title={t.title}"
            if t.duration_sec and t.duration_sec > 0 and query.startswith("title="):
                query += f", length={t.duration_sec}"
        elif "," not in t.artist and "," not in t.title:
            query = f"artist={t.artist}, title={t.title}"
            if t.duration_sec and t.duration_sec > 0:
                query += f", length={t.duration_sec}"
        else:
            query = f"{t.artist} - {t.title}"
        print(f"[Download] Ricerca traccia singola: {query}")
        return _run_sockseek([base_cmd[0], query, "-s", *base_cmd[1:]]), cfg.output_dir

    if effective_kind == "playlist":
        target_dir = cfg.output_dir / csv_path.stem
        print(f"[Download] Playlist -> {target_dir}")
        return _run_sockseek([base_cmd[0], str(csv_path), *base_cmd[1:]]), target_dir

    # Full Album mode: try complete album folder first, then fall back to track-by-track CSV.
    album_query = _build_album_query(export)
    album_cmd = [
        base_cmd[0],
        album_query,
        "-a",
        "--incomplete-album-action",
        "keep",
        *base_cmd[1:],
    ]
    if export.total_tracks and export.total_tracks >= 2:
        album_cmd.extend(["--album-track-count", f"{export.total_tracks}+"])

    print(f"[Download] Ricerca cartella Full Album su Soulseek: {album_query}")
    before_dirs = {p for p in cfg.output_dir.iterdir() if p.is_dir()}

    rc = _run_sockseek(album_cmd)
    new_audio = _new_audio_files(session_before_files, cfg.output_dir)
    _prune_empty_new_dirs(before_dirs, cfg.output_dir)

    if rc == 0 and new_audio:
        print(f"[OK] Full Album scaricato ({len(new_audio)} file) in: {new_audio[0].parent}")
        return 0, new_audio[0].parent

    if not export.tracks:
        if new_audio:
            print(f"[Avviso] Album scaricato parzialmente ({len(new_audio)} file) in: {new_audio[0].parent}")
            return rc, new_audio[0].parent
        print("[Errore] Nessuna cartella Full Album trovata e nessuna tracklist disponibile per il fallback.")
        return rc or 1, None

    if not new_audio:
        print("\n[Fallback] Cartella Full Album non trovata su Soulseek. Avvio download traccia-per-traccia...")
        fallback_rc = _run_sockseek([base_cmd[0], str(csv_path), *base_cmd[1:]])
        return fallback_rc, cfg.output_dir / csv_path.stem

    # Partial album folder downloaded: preserve already-fetched files and fetch missing tracks into same folder.
    partial_dir = new_audio[0].parent
    if partial_dir != cfg.output_dir:
        print(
            f"\n[Fallback] Scaricate {len(new_audio)} tracce nella cartella '{partial_dir.name}'. "
            "Cerco le tracce mancanti nella stessa cartella..."
        )
        matched_csv = cfg.lists_dir / f"{partial_dir.name}.csv"
        write_tracks_csv(export, matched_csv)
        fb_rc = _run_sockseek(
            [
                str(cfg.sockseek_bin),
                str(matched_csv),
                "-c",
                str(cfg.sockseek_conf),
                "-o",
                str(partial_dir.parent),
                "--skip-music-dir",
                str(partial_dir),
                "--skip-mode-music-dir",
                "name",
                *extra_args,
            ]
        )
        return fb_rc, partial_dir

    return rc, None


def download_collection(
    export: CollectionExport,
    cfg: PipelineConfig,
    export_only: bool = False,
    force_mode: str = "auto",
    convert_format: str | None = None,
    convert_aiff: bool | None = None,
    delete_original: bool = True,
    open_finder: bool = True,
) -> int:
    """Export tracklist CSV, run Sockseek natively without Docker, convert FLAC if requested, and open Finder."""
    if not cfg.sockseek_bin.is_file() and not export_only:
        raise FileNotFoundError(f"Binario sockseek non trovato: {cfg.sockseek_bin}")
    if not cfg.sockseek_conf.is_file() and not export_only:
        raise FileNotFoundError(f"Configurazione sockseek non trovata: {cfg.sockseek_conf}")

    csv_path = cfg.lists_dir / f"{export.name}.csv"
    if export.tracks:
        write_tracks_csv(export, csv_path)
        print(f"[Export] Salvata lista ({len(export.tracks)} tracce): {csv_path}")
    else:
        print(f"[Export] Album '{export.name}' senza tracklist dettagliata: ricerca diretta su Soulseek.")

    if export_only:
        return 0

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    effective_kind = export.kind
    if force_mode == "tracks" and export.tracks:
        effective_kind = "playlist"
    elif force_mode == "album" and export.kind == "playlist":
        effective_kind = "album"

    effective_convert = (
        normalize_convert_format(convert_format)
        if convert_format is not None
        else (normalize_convert_format(convert_aiff) if convert_aiff is not None else "flac")
    )

    session_before_files = _snapshot_files(cfg.output_dir)
    session_before_dirs = {p for p in cfg.output_dir.rglob("*") if p.is_dir()} if cfg.output_dir.is_dir() else set()
    rc, preferred_dir = _execute_download(
        export=export,
        effective_kind=effective_kind,
        csv_path=csv_path,
        cfg=cfg,
        session_before_files=session_before_files,
        convert_format=effective_convert,
    )
    _cleanup_non_audio_files(session_before_files, session_before_dirs, cfg.output_dir)
    _maybe_convert(cfg, effective_convert, session_before_files)
    if open_finder:
        _reveal_after_download(cfg, session_before_files, preferred_dir)
    return rc
