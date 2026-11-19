"""On-demand lightweight Ollama formatter for YouTube playlists and full albums."""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request

from config import PipelineConfig
from models import CollectionExport, Track
from url_utils import sanitize_filename


_PREFERRED_MODELS = (
    "qwen2.5:3b",
    "qwen2.5:1.5b",
    "qwen3:1.7b",
    "qwen3:4b",
    "llama3.2:3b",
    "gemma3:4b",
    "qwen2.5:7b",
    "qwen2.5:14b",
)

_GARBAGE_BRACKETS_RE = re.compile(
    r"[\(\[]\s*(?:official\s*(?:music\s*)?(?:video|audio|lyric\s*video|visualizer|mv)|"
    r"lyrics?|audio|hd|hq|4k|full\s*album|full\s*track|vinyl\s*rip|rare\s*\w+|"
    r"remaster(?:ed)?(?:\s*\d{4})?|\d{4})\s*[\)\]]",
    re.IGNORECASE,
)
_LEADING_NUM_RE = re.compile(r"^\s*(?:\d{1,3}\s*[.\)\-:]\s+)")
_TIMESTAMP_LINE_RE = re.compile(
    r"^\s*(?:[-*•]?\s*)?(?:\d{1,2}\s*[.\)]\s*)?"
    r"(?:(?P<ts1>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*[-–—|]?\s*(?P<title1>.+?)|"
    r"(?P<title2>.+?)\s*[-–—|(]\s*(?P<ts2>(?:\d{1,2}:)?\d{1,2}:\d{2})\)?)\s*$"
)
_ALBUM_KEYWORDS_RE = re.compile(r"\b(full\s+album|album\s+completo|full\s+ep)\b", re.IGNORECASE)


def parse_timestamp_seconds(ts: str) -> int:
    """Convert MM:SS or HH:MM:SS into integer seconds."""
    parts = [int(p) for p in ts.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def _channel_looks_like_artist(channel: str) -> bool:
    ch = (channel or "").strip()
    if not ch:
        return False
    lower = ch.lower()
    return ch.endswith("- Topic") or lower.endswith("vevo") or lower.endswith("official")


def parse_track_heuristic(raw_title: str, uploader: str = "", default_artist: str = "") -> Track:
    """Deterministic fallback parser when LLM is unavailable or misses an item."""
    cleaned = _GARBAGE_BRACKETS_RE.sub("", raw_title or "").strip()
    cleaned = _LEADING_NUM_RE.sub("", cleaned).strip(" -|")

    # Strip trailing "| Channel" or "Channel | Artist - Track" prefixes if unambiguous
    if " | " in cleaned:
        left, right = cleaned.split(" | ", 1)
        if " - " in right and " - " not in left:
            cleaned = right.strip()
        elif " - " in left and " - " not in right:
            cleaned = left.strip()

    for sep in (" - ", " – ", " — "):
        if sep in cleaned:
            artist, title = cleaned.split(sep, 1)
            artist, title = artist.strip(), title.strip()
            if artist and title:
                return Track(artist=artist, title=title, raw_title=raw_title, uploader=uploader)

    if default_artist:
        fallback_artist = default_artist
    elif _channel_looks_like_artist(uploader):
        fallback_artist = re.sub(r"\s*-\s*Topic$", "", uploader).strip()
        fallback_artist = re.sub(r"(?:VEVO|Official)$", "", fallback_artist, flags=re.IGNORECASE).strip()
    else:
        # Leave artist empty rather than polluting Soulseek search with a random re-uploader channel name.
        fallback_artist = ""

    return Track(artist=fallback_artist, title=cleaned or raw_title, raw_title=raw_title, uploader=uploader)


def extract_timestamp_lines(description: str, total_duration: int | None = None) -> list[Track]:
    """Extract track names and durations from timestamped lines in a video description."""
    entries: list[tuple[int, str]] = []
    for line in (description or "").splitlines():
        m = _TIMESTAMP_LINE_RE.match(line.strip())
        if not m:
            continue
        ts = m.group("ts1") or m.group("ts2")
        label = (m.group("title1") or m.group("title2") or "").strip(" -–—|")
        if ts and label and len(label) >= 2:
            entries.append((parse_timestamp_seconds(ts), label))

    if len(entries) < 3:
        return []

    entries.sort(key=lambda item: item[0])
    tracks: list[Track] = []
    for idx, (start_sec, label) in enumerate(entries):
        end_sec = entries[idx + 1][0] if idx + 1 < len(entries) else total_duration
        dur = int(end_sec - start_sec) if end_sec and end_sec > start_sec else None
        parsed = parse_track_heuristic(label)
        parsed.duration_sec = dur
        tracks.append(parsed)
    return tracks


def _parse_llm_json(raw_content: str) -> dict:
    text = (raw_content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Risposta JSON LLM non è un oggetto.")
    return data


class OllamaSession:
    """Starts `ollama serve` on demand and terminates it on exit if we launched it."""

    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self.available = False
        self.model: str = ""

    def _list_models(self, timeout: float = 1.5) -> list[str] | None:
        try:
            req = urllib.request.Request(f"{self.cfg.ollama_url}/api/tags")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", []) if m.get("name")]
        except Exception:
            return None

    def _pick_model(self, installed: list[str]) -> str:
        if not installed:
            return ""
        requested = self.cfg.ollama_model
        if requested and requested != "auto":
            if requested in installed:
                return requested
            for name in installed:
                if name.startswith(requested + ":"):
                    return name
        for preferred in _PREFERRED_MODELS:
            if preferred in installed:
                return preferred
        return installed[0]

    def __enter__(self) -> "OllamaSession":
        models = self._list_models()
        if models is None:
            try:
                self._proc = subprocess.Popen(
                    [self.cfg.ollama_bin, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                return self

            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                models = self._list_models(timeout=1.0)
                if models is not None:
                    break
                time.sleep(0.25)

        if models:
            self.model = self._pick_model(models)
            self.available = bool(self.model)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def chat_json(self, system_prompt: str, user_prompt: str, keep_alive: str | int = 0) -> dict:
        """Call Ollama /api/chat expecting a strict JSON object response."""
        payload = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "keep_alive": keep_alive,
                "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 1200},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.cfg.ollama_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=50) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body.get("message", {}).get("content", "")
        return _parse_llm_json(content)


def format_youtube_playlist_with_llm(
    cfg: PipelineConfig,
    playlist_title: str,
    playlist_uploader: str,
    playlist_id: str,
    raw_entries: list[dict],
    source_url: str,
) -> CollectionExport:
    """Format a YouTube playlist (or YouTube album playlist) using on-demand Ollama."""
    is_ytm_album_hint = playlist_id.startswith("OLAK5uy_") or playlist_id.startswith("MPREb_")
    clean_title_guess = re.sub(r"^\s*Album\s*-\s*", "", playlist_title or "", flags=re.IGNORECASE).strip()

    is_album = False
    album_artist = ""
    album_title = ""
    formatted_tracks: list[Track] = []
    used_llm = False

    with OllamaSession(cfg) as llm:
        if llm.available:
            print(f"[LLM] Formattazione playlist YouTube con Ollama ({llm.model})...")
            batch_size = cfg.llm_batch_size
            total_batches = (len(raw_entries) + batch_size - 1) // batch_size

            for batch_idx in range(total_batches):
                if total_batches > 1:
                    print(f"[LLM] Batch {batch_idx + 1}/{total_batches}...", flush=True)
                start = batch_idx * batch_size
                chunk = raw_entries[start : start + batch_size]
                is_last = batch_idx == total_batches - 1
                keep_alive: str | int = 0 if is_last else "30s"

                lines = []
                for offset, item in enumerate(chunk):
                    idx = start + offset
                    dur = item.get("duration")
                    dur_s = str(int(dur)) if isinstance(dur, (int, float)) and dur > 0 else "?"
                    extra = []
                    if item.get("artist_tag"):
                        extra.append(f"yt_artist={item['artist_tag']}")
                    if item.get("track_tag"):
                        extra.append(f"yt_track={item['track_tag']}")
                    if item.get("desc_snippet"):
                        extra.append(f"desc={item['desc_snippet']}")
                    extra_str = (" | " + " | ".join(extra)) if extra else ""
                    lines.append(
                        f"{idx} | title={item.get('title', '')} | channel={item.get('channel', '')} | dur={dur_s}s{extra_str}"
                    )

                classify_hint = ""
                if batch_idx == 0:
                    classify_hint = (
                        f"Playlist title: {playlist_title!r}\n"
                        f"Playlist uploader: {playlist_uploader!r}\n"
                        f"YouTube Music album ID hint: {is_ytm_album_hint}\n"
                        "Also determine if this entire playlist is a single full studio album/EP "
                        "(is_album: true/false, album_artist, album_title).\n"
                    )

                system_prompt = (
                    "You normalize YouTube music playlist items into clean Soulseek search metadata.\n"
                    "Rules:\n"
                    "- Remove channel spam, track numbers ('01.', '04 -'), '[Official Video]', '(Official Audio)', "
                    "'[HD]', '(Lyrics)', '[Visualizer]', '[Vinyl Rip]', country/year brackets like '[1974]' or '[Ethiopia]'.\n"
                    "- Preserve musically important qualifiers: '(Extended Mix)', '(Original Mix)', '(... Remix)', "
                    "'(Live)', '(feat. ...)', '(Acoustic)', '(Instrumental)'.\n"
                    "- CRITICAL: On YouTube, `channel` is VERY OFTEN a collector/archive/crate-digger uploader "
                    "(e.g. 'KaderFunkyman', 'VinylFrontier', 'Boiler Room') rather than the musical artist! "
                    "Use `yt_artist`/`desc` or the video title when present. Only use `channel` as artist if it ends in "
                    "'- Topic'/'VEVO' or clearly IS the musician. If the artist is unknown/uncertain, set \"artist\": \"\".\n"
                    "- Output strictly valid JSON matching:\n"
                    '{"is_album": false, "album_artist": "", "album_title": "", '
                    '"tracks": [{"idx": 0, "artist": "Artist", "title": "Song Title"}]}.'
                )
                user_prompt = classify_hint + "Items:\n" + "\n".join(lines)

                parsed_map: dict[int, tuple[str, str]] = {}
                try:
                    data = llm.chat_json(system_prompt, user_prompt, keep_alive=keep_alive)
                    used_llm = True
                    if batch_idx == 0:
                        is_album = bool(data.get("is_album")) or is_ytm_album_hint
                        album_artist = str(data.get("album_artist") or "").strip()
                        album_title = str(data.get("album_title") or "").strip()
                    for row in data.get("tracks") or []:
                        if not isinstance(row, dict):
                            continue
                        try:
                            idx = int(row["idx"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        a = str(row.get("artist") or "").strip()
                        t = str(row.get("title") or "").strip()
                        if t:
                            parsed_map[idx] = (a, t)
                except Exception as exc:
                    print(f"[LLM] Avviso batch {batch_idx + 1}: {exc} (uso fallback locale)")

                for offset, item in enumerate(chunk):
                    idx = start + offset
                    dur = item.get("duration")
                    dur_sec = int(dur) if isinstance(dur, (int, float)) and dur > 0 else None
                    raw_t = str(item.get("title") or "")
                    uploader = str(item.get("channel") or "")
                    if idx in parsed_map:
                        a, t = parsed_map[idx]
                        if not a and item.get("artist_tag"):
                            a = str(item["artist_tag"])
                        track = Track(
                            artist=a,
                            title=t,
                            album=str(item.get("album_tag") or (album_title if is_album else "")),
                            duration_sec=dur_sec,
                            raw_title=raw_t,
                            uploader=uploader,
                        )
                    else:
                        if item.get("artist_tag") and item.get("track_tag"):
                            track = Track(
                                artist=str(item["artist_tag"]),
                                title=str(item["track_tag"]),
                                album=str(item.get("album_tag") or ""),
                                duration_sec=dur_sec,
                                raw_title=raw_t,
                                uploader=uploader,
                            )
                        else:
                            track = parse_track_heuristic(raw_t, uploader, default_artist=album_artist)
                            track.duration_sec = dur_sec
                            if is_album and album_title:
                                track.album = album_title
                    formatted_tracks.append(track)
        else:
            print("[YouTube] Formattazione tracce con parser locale integrato (Ollama opzionale non rilevato).")
            is_album = is_ytm_album_hint or bool(_ALBUM_KEYWORDS_RE.search(playlist_title or ""))
            for item in raw_entries:
                dur = item.get("duration")
                dur_sec = int(dur) if isinstance(dur, (int, float)) and dur > 0 else None
                if item.get("artist_tag") and item.get("track_tag"):
                    track = Track(
                        artist=str(item["artist_tag"]),
                        title=str(item["track_tag"]),
                        album=str(item.get("album_tag") or ""),
                        duration_sec=dur_sec,
                        raw_title=str(item.get("title") or ""),
                        uploader=str(item.get("channel") or ""),
                    )
                else:
                    track = parse_track_heuristic(str(item.get("title") or ""), str(item.get("channel") or ""))
                    track.duration_sec = dur_sec
                formatted_tracks.append(track)

    if is_album:
        if not album_artist and formatted_tracks:
            album_artist = next((tr.artist for tr in formatted_tracks if tr.artist), "")
        if not album_title:
            guess = parse_track_heuristic(clean_title_guess, playlist_uploader)
            if " - " in clean_title_guess:
                album_artist = album_artist or guess.artist
                album_title = guess.title
            else:
                album_title = clean_title_guess or "Full Album"
        for tr in formatted_tracks:
            if not tr.album:
                tr.album = album_title
        display_name = f"{album_artist} - {album_title}" if album_artist else album_title
        return CollectionExport(
            kind="album",
            name=sanitize_filename(display_name, fallback="youtube_album"),
            artist=album_artist,
            album=album_title,
            total_tracks=len(formatted_tracks) or None,
            tracks=formatted_tracks,
            source_url=source_url,
            source_platform="youtube",
            used_llm=used_llm,
        )

    return CollectionExport(
        kind="playlist",
        name=sanitize_filename(clean_title_guess or "youtube_playlist", fallback="youtube_playlist"),
        tracks=formatted_tracks,
        total_tracks=len(formatted_tracks),
        source_url=source_url,
        source_platform="youtube",
        used_llm=used_llm,
    )


def format_youtube_video_with_llm(
    cfg: PipelineConfig,
    meta: dict,
    source_url: str,
) -> CollectionExport:
    """Classify and format a single YouTube video (full album video OR single song)."""
    raw_title = str(meta.get("title") or "")
    uploader = str(meta.get("uploader") or meta.get("channel") or "")
    duration = meta.get("duration")
    dur_sec = int(duration) if isinstance(duration, (int, float)) and duration > 0 else None
    chapters = meta.get("chapters") or []
    description = str(meta.get("description") or "")
    yt_artist = str(meta.get("artist") or "").strip()
    yt_album = str(meta.get("album") or "").strip()
    yt_track = str(meta.get("track") or "").strip()

    desc_tracks = extract_timestamp_lines(description, dur_sec)
    has_album_signals = (
        bool(_ALBUM_KEYWORDS_RE.search(raw_title))
        or len(chapters) >= 3
        or len(desc_tracks) >= 3
        or (dur_sec is not None and dur_sec >= 900)
    )

    chapter_lines: list[str] = []
    for i, ch in enumerate(chapters):
        st = ch.get("start_time")
        en = ch.get("end_time")
        ch_dur = int(en - st) if isinstance(st, (int, float)) and isinstance(en, (int, float)) and en > st else None
        chapter_lines.append(f"{i}. {ch.get('title', '')} ({ch_dur or '?'}s)")

    desc_excerpt_lines = [ln for ln in description.splitlines() if ln.strip()]
    desc_excerpt = "\n".join(desc_excerpt_lines[:80])[:4000]

    with OllamaSession(cfg) as llm:
        if llm.available:
            print(f"[LLM] Analisi video YouTube con Ollama ({llm.model})...")
            if not has_album_signals:
                system_prompt = (
                    "Extract clean Artist and Song Title from a single YouTube music video.\n"
                    "Remove '[Official Video]', '(Official Audio)', '(Lyrics)', '[HD]', '- Topic', '[Vinyl Rip]'. "
                    "Keep remixes/featuring. Do not treat archive channel uploaders as the artist; leave artist \"\" if unknown.\n"
                    'Output strictly JSON: {"artist": "...", "title": "..."}'
                )
                user_prompt = (
                    f"Video title: {raw_title}\n"
                    f"Uploader: {uploader}\n"
                    f"Metadata artist: {yt_artist}\n"
                    f"Metadata track: {yt_track}\n"
                    f"Description snippet: {desc_excerpt[:400]}"
                )
                try:
                    data = llm.chat_json(system_prompt, user_prompt, keep_alive=0)
                    artist = str(data.get("artist") or yt_artist or "").strip()
                    title = str(data.get("title") or "").strip()
                    if title:
                        tr = Track(
                            artist=artist,
                            title=title,
                            album=yt_album,
                            duration_sec=dur_sec,
                            raw_title=raw_title,
                            uploader=uploader,
                        )
                        display = f"{artist} - {title}" if artist else title
                        return CollectionExport(
                            kind="single_track",
                            name=sanitize_filename(display),
                            artist=artist,
                            album=yt_album,
                            total_tracks=1,
                            tracks=[tr],
                            source_url=source_url,
                            source_platform="youtube",
                            used_llm=True,
                        )
                except Exception as exc:
                    print(f"[LLM] Avviso singolo video: {exc} (uso fallback locale)")
            else:
                system_prompt = (
                    "Analyze a YouTube video (likely a Full Album upload or multi-track release).\n"
                    "Determine:\n"
                    "1. is_full_album (boolean): true if it is an album/EP upload, false if a single song.\n"
                    "2. album_artist and album_title (clean, without '[Full Album]', '(1972)', 'HQ', etc.).\n"
                    "3. tracks: extract clean individual tracks from Chapters or Description if listed, "
                    "including duration_sec (integer or null) when timestamps/durations are provided.\n"
                    "Output strictly valid JSON:\n"
                    '{"is_full_album": true, "album_artist": "...", "album_title": "...", '
                    '"tracks": [{"artist": "...", "title": "...", "duration_sec": 210}]}'
                )
                user_prompt = (
                    f"Video title: {raw_title}\n"
                    f"Uploader: {uploader}\n"
                    f"Total duration: {dur_sec or '?'}s\n"
                    f"Metadata artist/album: {yt_artist} / {yt_album}\n"
                    f"Chapters ({len(chapter_lines)}):\n" + "\n".join(chapter_lines[:60]) + "\n\n"
                    f"Description excerpt:\n{desc_excerpt}"
                )
                try:
                    data = llm.chat_json(system_prompt, user_prompt, keep_alive=0)
                    is_full_album = bool(data.get("is_full_album"))
                    album_artist = str(data.get("album_artist") or yt_artist or "").strip()
                    album_title = str(data.get("album_title") or yt_album or "").strip()
                    tracks: list[Track] = []
                    for row in data.get("tracks") or []:
                        if not isinstance(row, dict):
                            continue
                        t = str(row.get("title") or "").strip()
                        a = str(row.get("artist") or album_artist or "").strip()
                        d = row.get("duration_sec")
                        d_int = int(d) if isinstance(d, (int, float)) and d > 0 else None
                        if t:
                            tracks.append(
                                Track(
                                    artist=a,
                                    title=t,
                                    album=album_title if is_full_album else "",
                                    duration_sec=d_int,
                                    raw_title=t,
                                    uploader=uploader,
                                )
                            )

                    if is_full_album and (album_artist or album_title):
                        if not tracks and desc_tracks:
                            tracks = desc_tracks
                        display = f"{album_artist} - {album_title}" if album_artist and album_title else (album_title or album_artist)
                        return CollectionExport(
                            kind="album",
                            name=sanitize_filename(display, fallback="youtube_album"),
                            artist=album_artist,
                            album=album_title,
                            total_tracks=len(tracks) or None,
                            tracks=tracks,
                            source_url=source_url,
                            source_platform="youtube",
                            used_llm=True,
                        )
                    if tracks:
                        t0 = tracks[0]
                        if len(tracks) == 1:
                            t0.duration_sec = t0.duration_sec or dur_sec
                            display = f"{t0.artist} - {t0.title}" if t0.artist else t0.title
                            return CollectionExport(
                                kind="single_track",
                                name=sanitize_filename(display),
                                artist=t0.artist,
                                album=t0.album,
                                total_tracks=1,
                                tracks=[t0],
                                source_url=source_url,
                                source_platform="youtube",
                                used_llm=True,
                            )
                except Exception as exc:
                    print(f"[LLM] Avviso full album video: {exc} (uso fallback locale)")

    # Deterministic fallback
    if has_album_signals:
        cleaned_title = _ALBUM_KEYWORDS_RE.sub("", raw_title)
        guess = parse_track_heuristic(cleaned_title, uploader, default_artist=yt_artist)
        album_artist = yt_artist or guess.artist
        album_title = yt_album or guess.title
        fallback_tracks = list(desc_tracks)
        if not fallback_tracks and chapter_lines:
            for ch in chapters:
                st = ch.get("start_time")
                en = ch.get("end_time")
                ch_dur = int(en - st) if isinstance(st, (int, float)) and isinstance(en, (int, float)) and en > st else None
                tr = parse_track_heuristic(str(ch.get("title") or ""), uploader, default_artist=album_artist)
                tr.album = album_title
                tr.duration_sec = ch_dur
                fallback_tracks.append(tr)
        else:
            for tr in fallback_tracks:
                if not tr.artist and album_artist:
                    tr.artist = album_artist
                tr.album = album_title

        display = f"{album_artist} - {album_title}" if album_artist else album_title
        return CollectionExport(
            kind="album",
            name=sanitize_filename(display, fallback="youtube_album"),
            artist=album_artist,
            album=album_title,
            total_tracks=len(fallback_tracks) or None,
            tracks=fallback_tracks,
            source_url=source_url,
            source_platform="youtube",
            used_llm=False,
        )

    tr = parse_track_heuristic(f"{yt_artist} - {yt_track}" if yt_artist and yt_track else raw_title, uploader)
    tr.album = yt_album
    tr.duration_sec = dur_sec
    display = f"{tr.artist} - {tr.title}" if tr.artist else tr.title
    return CollectionExport(
        kind="single_track",
        name=sanitize_filename(display),
        artist=tr.artist,
        album=tr.album,
        total_tracks=1,
        tracks=[tr],
        source_url=source_url,
        source_platform="youtube",
        used_llm=False,
    )
