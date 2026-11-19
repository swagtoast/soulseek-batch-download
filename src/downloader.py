"""Sockseek download runner supporting playlists, single tracks, full-album fallback, and FLAC->AIFF."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

from config import PipelineConfig, normalize_convert_format
from converter import convert_downloaded_flacs
from models import CollectionExport, Track
from tagger import tag_downloaded_files


AUDIO_EXTENSIONS = {".flac", ".mp3", ".aiff", ".aif", ".wav", ".m4a", ".ogg", ".opus", ".alac"}
MISSING_TRACKS_REPORT_NAME = "BRANI_MANCANTI.txt"
PROTECTED_REPORT_NAMES = {MISSING_TRACKS_REPORT_NAME.lower()}


def write_tracks_csv(export: CollectionExport, csv_path: Path, include_length: bool | None = None) -> Path:
    """Write tracks in RFC 4180 CSV format compatible with Sockseek."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if include_length is None:
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


def _norm_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_only).strip()


def _match_score(track: Track, file_stem_norm: str) -> float:
    title_norm = _norm_text(track.title)
    if not title_norm or not file_stem_norm:
        return 0.0
    if title_norm in file_stem_norm:
        return 1.0
    t_tokens = [tok for tok in title_norm.split() if len(tok) >= 2 or tok.isdigit()]
    if not t_tokens:
        t_tokens = title_norm.split()
    f_tokens = set(file_stem_norm.split())
    if not t_tokens or not f_tokens:
        return 0.0
    hits = sum(1 for tok in t_tokens if tok in f_tokens)
    return hits / len(t_tokens)


def find_missing_tracks(expected_tracks: list[Track], downloaded_audio: list[Path]) -> list[Track]:
    """Identify which expected tracks are still missing among downloaded audio files (1-to-1 greedy match)."""
    if not expected_tracks:
        return []
    if not downloaded_audio:
        return list(expected_tracks)

    file_norms = [_norm_text(p.stem) for p in downloaded_audio]
    used_files: set[int] = set()
    missing: list[Track] = []

    for track in expected_tracks:
        best_idx = -1
        best_score = 0.0
        for idx, f_norm in enumerate(file_norms):
            if idx in used_files:
                continue
            sc = _match_score(track, f_norm)
            if sc > best_score:
                best_score = sc
                best_idx = idx
        if best_idx >= 0 and best_score >= 0.5:
            used_files.add(best_idx)
        else:
            missing.append(track)

    return missing


def _rescue_missing_tracks(
    missing_tracks: list[Track],
    target_dir: Path,
    cfg: PipelineConfig,
    convert_format: str,
) -> int:
    """Run a relaxed, desperate rescue pass for ONLY the missing tracks directly into target_dir."""
    if not missing_tracks:
        return 0
    names = ", ".join(f"{t.artist} - {t.title}" if t.artist else t.title for t in missing_tracks[:5])
    suffix = f" (+ altri {len(missing_tracks) - 5})" if len(missing_tracks) > 5 else ""
    print(f"\n[Recupero] Cerco {len(missing_tracks)} tracce mancanti con ricerca estesa: {names}{suffix}")

    target_dir.mkdir(parents=True, exist_ok=True)
    rescue_export = CollectionExport(
        kind="playlist",
        name=target_dir.name,
        tracks=missing_tracks,
        total_tracks=len(missing_tracks),
    )
    with tempfile.TemporaryDirectory(prefix="slsk_rescue_") as tmp:
        rescue_csv = Path(tmp) / f"{target_dir.name}.csv"
        write_tracks_csv(rescue_export, rescue_csv, include_length=False)
        cmd = [
            str(cfg.sockseek_bin),
            str(rescue_csv),
            "-c",
            str(cfg.sockseek_conf),
            "-o",
            str(target_dir.parent),
            "--no-write-index",
            *_format_search_args(convert_format),
            "--desperate",
            "--remove-ft",
            "--remove-brackets",
            "--length-tol",
            "-1",
            "--no-skip-existing",
        ]
        return _run_sockseek(cmd)


def _write_missing_tracks_report(
    target_dir: Path,
    export: CollectionExport,
    missing_tracks: list[Track],
    downloaded_count: int,
    expected_count: int,
) -> Path:
    """Write a readable BRANI_MANCANTI.txt report inside target_dir (intentional exception to audio-only cleanup)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / MISSING_TRACKS_REPORT_NAME
    lines = [
        "Soulseek Batch Download — Brani non trovati su Soulseek",
        f"Collezione: {export.name}",
        f"Scaricati: {downloaded_count}/{expected_count} (mancanti: {len(missing_tracks)})",
        "",
    ]
    for idx, tr in enumerate(missing_tracks, start=1):
        base = f"{tr.artist} - {tr.title}" if tr.artist else tr.title
        if tr.album and tr.album.lower() not in base.lower():
            base = f"{base} [{tr.album}]"
        lines.append(f"{idx:02d}. {base}")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[Controllo tracce] Elenco brani mancanti salvato in: {report_path}")
    return report_path


def _verify_and_rescue_tracks(
    export: CollectionExport,
    target_dir: Path,
    cfg: PipelineConfig,
    session_before_files: dict[Path, float],
    convert_format: str,
) -> None:
    """Verify downloaded track count against expected tracks and automatically rescue any missing tracks."""
    expected_tracks = export.tracks
    expected_count = max(export.total_tracks or 0, len(expected_tracks))
    if expected_count <= 0:
        return

    report_path = target_dir / MISSING_TRACKS_REPORT_NAME
    current_audio = _new_audio_files(session_before_files, cfg.output_dir)
    if len(current_audio) >= expected_count:
        print(f"[Controllo tracce] Completo: {len(current_audio)}/{expected_count} tracce scaricate.")
        if report_path.is_file():
            try:
                report_path.unlink()
            except OSError:
                pass
        return

    if not expected_tracks:
        print(f"[Controllo tracce] Avviso: scaricate {len(current_audio)}/{expected_count} tracce.")
        return

    missing = find_missing_tracks(expected_tracks, current_audio)
    if not missing:
        print(f"[Controllo tracce] Scaricate {len(current_audio)}/{expected_count} tracce.")
        return

    print(f"[Controllo tracce] Scaricate {len(current_audio)}/{expected_count} tracce — mancano {len(missing)} brani.")
    _rescue_missing_tracks(missing, target_dir, cfg, convert_format)

    after_audio = _new_audio_files(session_before_files, cfg.output_dir)
    still_missing = find_missing_tracks(expected_tracks, after_audio)
    if not still_missing or len(after_audio) >= expected_count:
        print(f"[Controllo tracce] Tutte le {len(after_audio)}/{expected_count} tracce scaricate!")
        if report_path.is_file():
            try:
                report_path.unlink()
            except OSError:
                pass
    else:
        missed_list = "; ".join(f"{t.artist} - {t.title}" if t.artist else t.title for t in still_missing)
        print(
            f"[Controllo tracce] Scaricate {len(after_audio)}/{expected_count} tracce. "
            f"Non trovate su Soulseek ({len(still_missing)}): {missed_list}"
        )
        _write_missing_tracks_report(
            target_dir=target_dir,
            export=export,
            missing_tracks=still_missing,
            downloaded_count=len(after_audio),
            expected_count=expected_count,
        )


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
        if path.name.lower() in PROTECTED_REPORT_NAMES:
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
            "--pref-max-bitrate",
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
    """Open a downloaded folder (or reveal a single file) in macOS Finder, Windows Explorer, or Linux File Manager."""
    if not target.exists():
        return
    if sys.platform == "darwin":
        cmd = ["/usr/bin/open", "-R", str(target)] if reveal else ["/usr/bin/open", str(target)]
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif os.name == "nt":
        if reveal:
            subprocess.run(
                ["explorer.exe", f"/select,{target}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform.startswith("linux") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        if reveal and target.is_file():
            for fm, flag in (("nautilus", "--select"), ("dolphin", "--select")):
                fm_bin = shutil.which(fm)
                if fm_bin:
                    subprocess.Popen(
                        [fm_bin, flag, str(target)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return
            open_target = target.parent
        else:
            open_target = target
        xdg = shutil.which("xdg-open")
        if xdg:
            subprocess.Popen(
                [xdg, str(open_target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )


def _reveal_after_download(
    cfg: PipelineConfig,
    before_files: dict[Path, float],
    preferred_dir: Path | None,
) -> None:
    fm_label = "Esplora File" if os.name == "nt" else ("File Manager" if sys.platform.startswith("linux") else "Finder")
    post_audio = _new_audio_files(before_files, cfg.output_dir)
    if len(post_audio) == 1 and post_audio[0].parent == cfg.output_dir:
        print(f"[{fm_label}] Apro in {fm_label}: {post_audio[0]}")
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
        print(f"[{fm_label}] Apro cartella in {fm_label}: {target_dir}")
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
        print(f"[Download] Playlist ({len(export.tracks)} tracce) -> {target_dir}")
        rc = _run_sockseek([base_cmd[0], str(csv_path), *base_cmd[1:]])
        _verify_and_rescue_tracks(export, target_dir, cfg, session_before_files, convert_format)
        return rc, target_dir

    # Full Album mode: try complete album folder first, verify track count, and rescue any missing tracks.
    expected_count = max(export.total_tracks or 0, len(export.tracks))
    album_query = _build_album_query(export)
    album_cmd = [
        base_cmd[0],
        album_query,
        "-a",
        "--incomplete-album-action",
        "keep",
        *base_cmd[1:],
    ]
    if expected_count >= 2:
        album_cmd.extend(["--album-track-count", f"{expected_count}+"])

    count_label = f" ({expected_count} tracce attese)" if expected_count else ""
    print(f"[Download] Ricerca cartella Full Album su Soulseek: {album_query}{count_label}")
    before_dirs = {p for p in cfg.output_dir.iterdir() if p.is_dir()}

    rc = _run_sockseek(album_cmd)
    new_audio = _new_audio_files(session_before_files, cfg.output_dir)
    _prune_empty_new_dirs(before_dirs, cfg.output_dir)

    if rc == 0 and new_audio and (expected_count == 0 or len(new_audio) >= expected_count):
        print(f"[OK] Full Album completo ({len(new_audio)}/{expected_count or len(new_audio)} tracce) in: {new_audio[0].parent}")
        return 0, new_audio[0].parent

    if not export.tracks:
        if new_audio:
            print(f"[Avviso] Album scaricato ({len(new_audio)} file) in: {new_audio[0].parent}")
            return rc, new_audio[0].parent
        print("[Errore] Nessuna cartella Full Album trovata e nessuna tracklist disponibile per il fallback.")
        return rc or 1, None

    if not new_audio:
        print("\n[Fallback] Cartella Full Album non trovata su Soulseek. Avvio download traccia-per-traccia...")
        target_dir = cfg.output_dir / csv_path.stem
        fallback_rc = _run_sockseek([base_cmd[0], str(csv_path), *base_cmd[1:]])
        _verify_and_rescue_tracks(export, target_dir, cfg, session_before_files, convert_format)
        return fallback_rc, target_dir

    # Partial album folder downloaded (including rc == 0 with fewer tracks than expected):
    # keep fetched files and rescue ONLY the missing tracks directly into the same folder.
    partial_dir = new_audio[0].parent
    if partial_dir != cfg.output_dir:
        _verify_and_rescue_tracks(export, partial_dir, cfg, session_before_files, convert_format)
        return 0, partial_dir

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
    auto_tag: bool = True,
) -> int:
    """Export tracklist CSV, run Sockseek natively without Docker, convert FLAC if requested, tag audio, and open Finder."""
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
    if auto_tag:
        post_audio = _new_audio_files(session_before_files, cfg.output_dir)
        if post_audio:
            try:
                tag_downloaded_files(post_audio, export, cfg)
            except Exception as exc:
                print(f"[Tagger] Avviso: tagging saltato ({exc})")
    if open_finder:
        _reveal_after_download(cfg, session_before_files, preferred_dir)
    return rc
