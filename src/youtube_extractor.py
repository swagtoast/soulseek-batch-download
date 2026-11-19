"""YouTube playlist and single/full-album video metadata extractor via yt-dlp."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess

from config import PipelineConfig
from llm_formatter import format_youtube_playlist_with_llm, format_youtube_video_with_llm
from models import CollectionExport
from url_utils import ParsedUrl


_AUTH_HINT_RE = re.compile(
    r"(?:sign in|confirm you.?re not a bot|cookies|private|age[- ]restricted|login|members-only)",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"\s[-–—]\s")


class YouTubeExtractionError(RuntimeError):
    """Raised when yt-dlp cannot extract metadata for a YouTube URL."""


def _base_yt_dlp_cmd(cfg: PipelineConfig) -> list[str]:
    cmd = [
        cfg.yt_dlp_bin,
        "--ignore-config",
        "--no-warnings",
        "--ignore-no-formats-error",
    ]
    if cfg.deno_bin and Path(cfg.deno_bin).is_file():
        cmd.extend(["--js-runtimes", f"deno:{cfg.deno_bin}"])
    return cmd


def _parse_json_output(raw_stdout: str) -> dict | None:
    text = (raw_stdout or "").strip()
    if not text or text == "null":
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
        return None


def _run_yt_dlp_json(cfg: PipelineConfig, args: list[str]) -> dict:
    base = _base_yt_dlp_cmd(cfg)
    proc = subprocess.run([*base, *args], capture_output=True, text=True, check=False)
    data = _parse_json_output(proc.stdout)
    if proc.returncode == 0 and data is not None:
        return data

    err = (proc.stderr or proc.stdout or "").strip()
    if data is not None and data.get("entries"):
        return data

    # Retry with browser cookies only if YouTube explicitly blocks anonymous extraction.
    if _AUTH_HINT_RE.search(err) or data is None:
        for browser in ("chrome", "safari"):
            retry = subprocess.run(
                [*base, "--cookies-from-browser", browser, *args],
                capture_output=True,
                text=True,
                check=False,
            )
            retry_data = _parse_json_output(retry.stdout)
            if retry_data is not None and (retry.returncode == 0 or retry_data.get("entries") or retry_data.get("title")):
                return retry_data
            if retry.stderr:
                err = retry.stderr.strip()

    if data is not None and (data.get("title") or data.get("entries")):
        return data
    raise YouTubeExtractionError(f"yt-dlp ha restituito errore ({proc.returncode}): {err or 'risposta vuota'}")


def _enrich_single_entry(cfg: PipelineConfig, entry: dict) -> None:
    vid = entry["id"]
    try:
        meta = _run_yt_dlp_json(
            cfg,
            ["--dump-single-json", "--skip-download", f"https://www.youtube.com/watch?v={vid}"],
        )
    except Exception:
        return

    yt_artist = str(meta.get("artist") or "").strip()
    yt_track = str(meta.get("track") or "").strip()
    if yt_artist:
        entry["artist_tag"] = yt_artist
    if yt_track:
        entry["track_tag"] = yt_track
    if meta.get("album"):
        entry["album_tag"] = str(meta["album"]).strip()
    if not (yt_artist and yt_track):
        desc = str(meta.get("description") or "").strip()
        if desc:
            entry["desc_snippet"] = " | ".join(line.strip() for line in desc.splitlines()[:4] if line.strip())[:160]
    if not entry.get("duration") and meta.get("duration"):
        entry["duration"] = meta.get("duration")


def _enrich_ambiguous_entries(cfg: PipelineConfig, raw_entries: list[dict], max_lookups: int = 5) -> None:
    """Fetch full metadata in parallel for playlist tracks whose title lacks 'Artist - Title'."""
    ambiguous = [
        entry
        for entry in raw_entries
        if entry.get("id") and not _SEPARATOR_RE.search(entry.get("title") or "")
    ]
    if not ambiguous:
        return

    targets = ambiguous[:max_lookups]
    hw_workers = cfg.hardware.recommended_yt_workers if cfg.hardware else 4
    with ThreadPoolExecutor(max_workers=max(1, min(hw_workers, len(targets)))) as pool:
        list(pool.map(lambda e: _enrich_single_entry(cfg, e), targets))


def extract_youtube(parsed: ParsedUrl, cfg: PipelineConfig) -> CollectionExport:
    """Extract and LLM-normalize a YouTube playlist, album playlist, or full-album/single video."""
    if parsed.kind == "youtube_playlist":
        meta = _run_yt_dlp_json(
            cfg,
            ["--ignore-errors", "--flat-playlist", "--dump-single-json", parsed.url],
        )

        # Some YouTube URLs resolve directly to a single video rather than a playlist.
        if meta.get("_type") == "video" and not meta.get("entries"):
            return format_youtube_video_with_llm(cfg=cfg, meta=meta, source_url=parsed.url)

        playlist_title = str(meta.get("title") or "")
        playlist_uploader = str(meta.get("uploader") or meta.get("channel") or "")
        raw_entries: list[dict] = []
        for entry in meta.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            if not title or title in {"[Deleted video]", "[Private video]", "[Unavailable video]"}:
                continue
            raw_entries.append(
                {
                    "id": str(entry.get("id") or "").strip(),
                    "title": title,
                    "channel": str(entry.get("channel") or entry.get("uploader") or ""),
                    "duration": entry.get("duration"),
                }
            )
        if not raw_entries:
            raise YouTubeExtractionError("La playlist YouTube è vuota, privata o non accessibile.")

        _enrich_ambiguous_entries(cfg, raw_entries)
        return format_youtube_playlist_with_llm(
            cfg=cfg,
            playlist_title=playlist_title,
            playlist_uploader=playlist_uploader,
            playlist_id=parsed.item_id,
            raw_entries=raw_entries,
            source_url=parsed.url,
        )

    if parsed.kind == "youtube_video":
        meta = _run_yt_dlp_json(cfg, ["--dump-single-json", "--skip-download", parsed.url])
        return format_youtube_video_with_llm(cfg=cfg, meta=meta, source_url=parsed.url)

    raise YouTubeExtractionError(f"Tipo YouTube non supportato: {parsed.kind}")
