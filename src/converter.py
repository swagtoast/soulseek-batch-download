"""Safe post-download FLAC -> AIFF converter preserving metadata, cover art, and MP3 files."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import subprocess
import uuid


def _probe_cover_codec(ffprobe_bin: str, source: Path) -> str | None:
    """Return attached cover art codec ('png', 'mjpeg', etc.) if present."""
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    for stream in info.get("streams") or []:
        if stream.get("codec_type") == "video":
            disp = stream.get("disposition") or {}
            if disp.get("attached_pic") == 1:
                return str(stream.get("codec_name") or "png")
    return None


def _verify_aiff(ffprobe_bin: str, target: Path) -> bool:
    if not target.is_file() or target.stat().st_size <= 0:
        return False
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    streams = info.get("streams") or []
    return any(
        s.get("codec_type") == "audio" and str(s.get("codec_name") or "").startswith("pcm_")
        for s in streams
    )


def _unique_aiff_path(flac_path: Path) -> Path:
    candidate = flac_path.with_suffix(".aiff")
    if not candidate.exists():
        return candidate
    idx = 1
    while True:
        alt = flac_path.with_name(f"{flac_path.stem} ({idx}).aiff")
        if not alt.exists():
            return alt
        idx += 1


def _build_ffmpeg_args(
    ffmpeg_bin: str,
    src: Path,
    temp_out: Path,
    cover_codec: str | None,
) -> list[str]:
    args = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-map",
        "0:a:0",
    ]
    if cover_codec:
        args.extend(["-map", "0:v:0"])
        if cover_codec in {"png", "mjpeg"}:
            args.extend(["-c:v", "copy"])
        else:
            args.extend(["-c:v", "png"])
        args.extend(["-disposition:v:0", "attached_pic"])
    else:
        args.append("-vn")

    args.extend(
        [
            "-map_metadata",
            "0",
            "-c:a",
            "pcm_s24be",
            "-write_id3v2",
            "1",
            "-id3v2_version",
            "3",
            "-f",
            "aiff",
            str(temp_out),
        ]
    )
    return args


def _update_index_csv(replacements: dict[Path, Path]) -> None:
    """Update nearby Sockseek _index.csv files so converted .flac entries point to .aiff."""
    if not replacements:
        return
    dirs_seen: set[Path] = set()
    for src in replacements:
        dirs_seen.add(src.parent)
        dirs_seen.add(src.parent.parent)

    for folder in dirs_seen:
        index_path = folder / "_index.csv"
        if not index_path.is_file():
            continue
        try:
            with index_path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
        except OSError:
            continue
        if not rows:
            continue

        changed = False
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            rel = row[0]
            candidate = (folder / rel).resolve()
            for old_path, new_path in replacements.items():
                if candidate == old_path.resolve():
                    try:
                        new_rel = "./" + str(new_path.resolve().relative_to(folder.resolve()))
                    except ValueError:
                        new_rel = str(new_path)
                    row[0] = new_rel
                    changed = True
                    break

        if changed:
            tmp_index = index_path.with_name(f"._index.tmp-{uuid.uuid4().hex[:6]}.csv")
            try:
                with tmp_index.open("w", encoding="utf-8", newline="") as f:
                    csv.writer(f).writerows(rows)
                shutil.move(str(tmp_index), str(index_path))
            except OSError:
                tmp_index.unlink(missing_ok=True)


def convert_downloaded_flacs_to_aiff(
    downloaded_audio: list[Path],
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> tuple[int, int]:
    """Convert downloaded .flac files to 24-bit AIFF and delete original .flac files; leave .mp3 untouched.

    Returns (converted_count, failed_count).
    """
    flacs = [p for p in downloaded_audio if p.is_file() and p.suffix.lower() == ".flac"]
    if not flacs:
        mp3_count = sum(1 for p in downloaded_audio if p.suffix.lower() == ".mp3")
        if mp3_count:
            print(f"[AIFF] Nessun file .flac da convertire ({mp3_count} file .mp3 mantenuti invariati).")
        return (0, 0)

    print(f"\n[AIFF] Conversione di {len(flacs)} file .flac in AIFF (PCM 24-bit, massima qualità)...")
    converted = 0
    failed = 0
    replacements: dict[Path, Path] = {}

    for idx, flac_path in enumerate(flacs, start=1):
        temp_aiff = flac_path.with_name(f".{flac_path.stem}.converting-{uuid.uuid4().hex[:8]}.aiff")
        cover_codec = _probe_cover_codec(ffprobe_bin, flac_path)

        try:
            proc = subprocess.run(
                _build_ffmpeg_args(ffmpeg_bin, flac_path, temp_aiff, cover_codec),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0 and cover_codec is not None:
                temp_aiff.unlink(missing_ok=True)
                proc = subprocess.run(
                    _build_ffmpeg_args(ffmpeg_bin, flac_path, temp_aiff, None),
                    capture_output=True,
                    text=True,
                    check=False,
                )

            if proc.returncode != 0 or not _verify_aiff(ffprobe_bin, temp_aiff):
                err = (proc.stderr or "").strip() or "verifica ffprobe fallita"
                print(f"  [{idx}/{len(flacs)}] ERRORE {flac_path.name}: {err} (FLAC originale mantenuto)")
                failed += 1
                continue

            final_aiff = _unique_aiff_path(flac_path)
            shutil.move(str(temp_aiff), str(final_aiff))
            flac_path.unlink()
            replacements[flac_path] = final_aiff
            converted += 1
            print(f"  [{idx}/{len(flacs)}] {flac_path.name} -> {final_aiff.name}")
        except Exception as exc:
            print(f"  [{idx}/{len(flacs)}] ERRORE {flac_path.name}: {exc} (FLAC originale mantenuto)")
            failed += 1
        finally:
            temp_aiff.unlink(missing_ok=True)

    _update_index_csv(replacements)
    mp3_count = sum(1 for p in downloaded_audio if p.suffix.lower() == ".mp3")
    if mp3_count:
        print(f"[AIFF] Mantenuti invariati {mp3_count} file .mp3.")
    print(f"[AIFF] Completato: {converted} convertiti in AIFF, {failed} errori.")
    return (converted, failed)
