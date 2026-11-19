"""Safe post-download audio converter (FLAC/WAV/AIFF -> MP3 320 kbps, AIFF 24-bit, or FLAC) preserving metadata & cover art."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import subprocess
import uuid


_CONVERTIBLE_SOURCES: dict[str, set[str]] = {
    "mp3": {".flac", ".wav", ".aiff", ".aif", ".m4a", ".alac", ".ogg", ".opus"},
    "aiff": {".flac", ".wav", ".alac"},
    "flac": {".wav", ".aiff", ".aif", ".alac"},
}


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


def _verify_converted(ffprobe_bin: str, target: Path, target_format: str) -> bool:
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
    if target_format == "aiff":
        return any(
            s.get("codec_type") == "audio" and str(s.get("codec_name") or "").startswith("pcm_")
            for s in streams
        )
    if target_format == "mp3":
        return any(
            s.get("codec_type") == "audio" and str(s.get("codec_name") or "") == "mp3"
            for s in streams
        )
    if target_format == "flac":
        return any(
            s.get("codec_type") == "audio" and str(s.get("codec_name") or "") == "flac"
            for s in streams
        )
    return False


def _unique_target_path(flac_path: Path, suffix: str) -> Path:
    candidate = flac_path.with_suffix(suffix)
    if not candidate.exists():
        return candidate
    idx = 1
    while True:
        alt = flac_path.with_name(f"{flac_path.stem} ({idx}){suffix}")
        if not alt.exists():
            return alt
        idx += 1


def _build_ffmpeg_args(
    ffmpeg_bin: str,
    src: Path,
    temp_out: Path,
    target_format: str,
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
        if target_format == "mp3":
            args.extend(["-c:v", "copy" if cover_codec in {"png", "mjpeg"} else "mjpeg"])
        else:
            args.extend(["-c:v", "copy" if cover_codec in {"png", "mjpeg"} else "png"])
        args.extend(["-disposition:v:0", "attached_pic"])
    else:
        args.append("-vn")

    if target_format == "mp3":
        args.extend(
            [
                "-map_metadata",
                "0",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "320k",
                "-id3v2_version",
                "3",
                "-write_id3v1",
                "1",
                "-f",
                "mp3",
                str(temp_out),
            ]
        )
    elif target_format == "flac":
        args.extend(
            [
                "-map_metadata",
                "0",
                "-c:a",
                "flac",
                "-f",
                "flac",
                str(temp_out),
            ]
        )
    else:
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
    """Update nearby Sockseek _index.csv files so converted .flac entries point to the converted files."""
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


def convert_downloaded_flacs(
    downloaded_audio: list[Path],
    target_format: str,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    delete_original: bool = True,
) -> tuple[int, int]:
    """Convert downloaded audio files to the requested format: 'mp3' (320 kbps), 'aiff' (24-bit PCM), or 'flac'.

    - target_format='mp3': converts .flac, .wav, .aiff/.aif, .m4a/.alac, .ogg, .opus -> .mp3 (320 kbps CBR).
    - target_format='aiff': converts .flac, .wav, .alac -> .aiff (PCM 24-bit); keeps already-.mp3 untouched.
    - target_format='flac': converts uncompressed .wav, .aiff/.aif, .alac -> .flac (lossless); keeps .flac & .mp3 untouched.
    Deletes original source files only when delete_original=True and ffprobe verification succeeds.
    Returns (converted_count, failed_count).
    """
    fmt = (target_format or "flac").strip().lower().lstrip(".")
    if fmt == "none":
        fmt = "flac"
    if fmt not in _CONVERTIBLE_SOURCES:
        return (0, 0)

    suffix_map = {"mp3": ".mp3", "aiff": ".aiff", "flac": ".flac"}
    tag_map = {"mp3": "MP3 320", "aiff": "AIFF", "flac": "FLAC"}
    desc_map = {
        "mp3": "MP3 (320 kbps CBR)",
        "aiff": "AIFF (PCM 24-bit, massima qualità)",
        "flac": "FLAC (lossless)",
    }
    suffix = suffix_map[fmt]
    tag = tag_map[fmt]
    desc = desc_map[fmt]
    eligible_exts = _CONVERTIBLE_SOURCES[fmt]

    sources = [p for p in downloaded_audio if p.is_file() and p.suffix.lower() in eligible_exts]
    if not sources:
        mp3_count = sum(1 for p in downloaded_audio if p.suffix.lower() == ".mp3")
        if mp3_count and fmt != "flac":
            print(f"[{tag}] Nessun file da convertire ({mp3_count} file .mp3 mantenuti invariati).")
        return (0, 0)

    keep_note = " — elimino originali" if delete_original else " — mantengo originali"
    print(f"\n[{tag}] Conversione di {len(sources)} file audio in {desc}{keep_note}...")
    converted = 0
    failed = 0
    replacements: dict[Path, Path] = {}

    for idx, src_path in enumerate(sources, start=1):
        temp_out = src_path.with_name(f".{src_path.stem}.converting-{uuid.uuid4().hex[:8]}{suffix}")
        cover_codec = _probe_cover_codec(ffprobe_bin, src_path)

        try:
            proc = subprocess.run(
                _build_ffmpeg_args(ffmpeg_bin, src_path, temp_out, fmt, cover_codec),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0 and cover_codec is not None:
                temp_out.unlink(missing_ok=True)
                proc = subprocess.run(
                    _build_ffmpeg_args(ffmpeg_bin, src_path, temp_out, fmt, None),
                    capture_output=True,
                    text=True,
                    check=False,
                )

            if proc.returncode != 0 or not _verify_converted(ffprobe_bin, temp_out, fmt):
                err = (proc.stderr or "").strip() or "verifica ffprobe fallita"
                print(f"  [{idx}/{len(sources)}] ERRORE {src_path.name}: {err} (file originale mantenuto)")
                failed += 1
                continue

            final_out = _unique_target_path(src_path, suffix)
            shutil.move(str(temp_out), str(final_out))
            if delete_original:
                src_path.unlink()
                replacements[src_path] = final_out
            converted += 1
            print(f"  [{idx}/{len(sources)}] {src_path.name} -> {final_out.name}")
        except Exception as exc:
            print(f"  [{idx}/{len(sources)}] ERRORE {src_path.name}: {exc} (file originale mantenuto)")
            failed += 1
        finally:
            temp_out.unlink(missing_ok=True)

    if delete_original:
        _update_index_csv(replacements)
    mp3_count = sum(1 for p in downloaded_audio if p.suffix.lower() == ".mp3")
    if mp3_count and fmt != "mp3":
        print(f"[{tag}] Mantenuti invariati {mp3_count} file .mp3 originali.")
    print(f"[{tag}] Completato: {converted} convertiti in {tag}, {failed} errori.")
    return (converted, failed)


def convert_downloaded_flacs_to_aiff(
    downloaded_audio: list[Path],
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> tuple[int, int]:
    """Backwards-compatible wrapper converting downloaded .flac files to 24-bit AIFF."""
    return convert_downloaded_flacs(
        downloaded_audio=downloaded_audio,
        target_format="aiff",
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
    )
