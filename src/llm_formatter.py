"""On-demand lightweight LLM formatter (fine-tuned MiniCPM5-2B MLX + Ollama fallback) for YouTube."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

from config import PipelineConfig
from models import CollectionExport, Track
from url_utils import sanitize_filename


_PREFERRED_MODELS = (
    "minicpm5-2b:latest",
    "minicpm5-2b",
    "qwen2.5:3b",
    "qwen2.5:1.5b",
    "qwen3:1.7b",
    "qwen3:4b",
    "llama3.2:3b",
    "gemma3:4b",
    "qwen2.5:7b",
    "qwen2.5:14b",
)

PLAYLIST_SYSTEM_PROMPT = (
    "You normalize YouTube music playlist items into clean Soulseek search metadata.\n"
    "Rules:\n"
    "- Remove emojis, BPM tags ('128 BPM'), release years ('(1980)', '*2000*', '[1974]'), track numbers ('01.', 'A1.'), "
    "'[Official Video]', '(Official Audio)', '[HD]', '[HQ]', '(Lyrics)', '[Visualizer]', '[Vinyl Rip]', "
    "country brackets like '[Ethiopia]', and trailing punctuation like trailing periods.\n"
    "- Preserve musically important qualifiers: '(Original Mix)', '(Extended Mix)', '(... Mix)', '(... Edit)', "
    "'(... Remix)', '(... Interpretation)', '(Live)', '(Acoustic)', '(Instrumental)', '(feat. ...)'.\n"
    "- Handle no-space hyphens ('Artist-Title'), extra spaces around hyphens, 'Artist feat. Guest - Title', "
    "and reversed 'Title - Artist' entries.\n"
    "- CRITICAL: On YouTube, `channel` is very often a collector/archive uploader rather than the musical artist. "
    "Use `yt_artist`/`desc` or the video title when present. Only use `channel` as artist if it ends in '- Topic'/'VEVO' "
    "or clearly IS the musician. If the artist is genuinely unknown, set \"artist\": \"\".\n"
    "- Determine if the entire playlist is a single studio album/EP (`is_album`, `album_artist`, `album_title`). "
    "A 'Best Of', 'Greatest Hits', or mixed compilation playlist is NOT a single studio album (`is_album`: false).\n"
    "- Output strictly valid JSON matching:\n"
    '{"is_album": false, "album_artist": "", "album_title": "", '
    '"tracks": [{"idx": 0, "artist": "Artist", "title": "Song Title"}]}.'
)

VIDEO_SYSTEM_PROMPT = (
    "Analyze a YouTube video (likely a Full Album upload or multi-track release).\n"
    "Determine:\n"
    "1. is_full_album (boolean): true if it is an album/EP upload, false if a single song.\n"
    "2. album_artist and album_title (clean, without '[Full Album]', '(1972)', 'HQ', etc.).\n"
    "3. tracks: extract EVERY single individual track from Chapters, Description, or Top Comments if listed "
    "(do NOT omit any track, including the first and the last track), including duration_sec (integer or null) "
    "when timestamps/durations are provided.\n"
    "Output strictly valid JSON:\n"
    '{"is_full_album": true, "album_artist": "...", "album_title": "...", '
    '"tracks": [{"artist": "...", "title": "...", "duration_sec": 210}]}'
)

SINGLE_VIDEO_SYSTEM_PROMPT = (
    "Extract clean Artist and Song Title from a single YouTube music video.\n"
    "Remove '[Official Video]', '(Official Audio)', '(Lyrics)', '[HD]', '- Topic', '[Vinyl Rip]'. "
    "Keep remixes/featuring. Do not treat archive channel uploaders as the artist; leave artist \"\" if unknown.\n"
    'Output strictly JSON: {"artist": "...", "title": "..."}'
)

_GARBAGE_BRACKETS_RE = re.compile(
    r"[\(\[]\s*(?:official\s*(?:music\s*)?(?:video|audio|lyric\s*video|visualizer|mv)|"
    r"lyrics?|audio|hd|hq|4k|full\s*album|full\s*track|vinyl\s*rip|rare\s*\w+|"
    r"remaster(?:ed)?(?:\s*\d{4})?|\d{4})\s*[\)\]]",
    re.IGNORECASE,
)
_LEADING_NUM_RE = re.compile(r"^\s*(?:[A-Da-d]\d\s*[.\)\-:]?\s+|\d{1,3}\s*[.\)\-:]\s+)")
_TIMESTAMP_LINE_RE = re.compile(
    r"^\s*(?:[-*•]?\s*)?(?:[A-Da-d]\d\s*[.\)\-:]?\s+|\d{1,2}\s*[.\)\-:]\s*)?"
    r"(?:[\(\[]?(?P<ts1>(?:\d{1,2}:)?\d{1,2}:\d{2})[\)\]]?\s*[-–—|:.]?\s*(?P<title1>.+?)|"
    r"(?P<title2>.+?)\s*(?:[-–—|(\[]\s*|\s+)(?P<ts2>(?:\d{1,2}:)?\d{1,2}:\d{2})[\)\]]?)\s*$"
)
_NUMBERED_TRACK_LINE_RE = re.compile(r"^\s*(?P<num>\d{1,2})\s*[.\)\-:]\s+(?P<title>.+?)\s*$")
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
    """Extract track names and durations from timestamped lines in a description or comment."""
    entries: list[tuple[int, str]] = []
    for line in (description or "").splitlines():
        m = _TIMESTAMP_LINE_RE.match(line.strip())
        if not m:
            continue
        ts = m.group("ts1") or m.group("ts2")
        label = (m.group("title1") or m.group("title2") or "").strip(" -–—|")
        if ts and label and len(label) >= 2 and not label.lower().startswith(("http://", "https://")):
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


def extract_numbered_tracklist(text: str) -> list[Track]:
    """Extract a clean sequential numbered tracklist (1. ..., 2. ..., N. ...) when timestamps are absent."""
    best_run: list[str] = []
    cur_run: list[str] = []
    expected_num = 1

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if len(cur_run) > len(best_run):
                best_run = list(cur_run)
            cur_run = []
            expected_num = 1
            continue
        m = _NUMBERED_TRACK_LINE_RE.match(line)
        if m:
            num = int(m.group("num"))
            title = m.group("title").strip(" -–—|")
            if num == expected_num and len(title) >= 2 and not title.lower().startswith(("http://", "https://")):
                cur_run.append(title)
                expected_num += 1
                continue
            if num == 1 and len(title) >= 2:
                if len(cur_run) > len(best_run):
                    best_run = list(cur_run)
                cur_run = [title]
                expected_num = 2
                continue
        if len(cur_run) > len(best_run):
            best_run = list(cur_run)
        cur_run = []
        expected_num = 1

    if len(cur_run) > len(best_run):
        best_run = list(cur_run)

    if len(best_run) < 4:
        return []
    return [parse_track_heuristic(lbl) for lbl in best_run]


def _extract_best_reference_tracks(
    chapters: list[dict],
    description: str,
    top_comments: list[str],
    total_duration: int | None,
    uploader: str,
) -> tuple[list[Track], str]:
    """Pick the richest tracklist found across Chapters, Description, and Top Comments."""
    candidates: list[tuple[list[Track], str]] = []

    if len(chapters) >= 3:
        ch_tracks: list[Track] = []
        for ch in chapters:
            st = ch.get("start_time")
            en = ch.get("end_time")
            ch_dur = int(en - st) if isinstance(st, (int, float)) and isinstance(en, (int, float)) and en > st else None
            tr = parse_track_heuristic(str(ch.get("title") or ""), uploader)
            tr.duration_sec = ch_dur
            if tr.title:
                ch_tracks.append(tr)
        if len(ch_tracks) >= 3:
            candidates.append((ch_tracks, "chapters"))

    desc_ts = extract_timestamp_lines(description, total_duration)
    if len(desc_ts) >= 3:
        candidates.append((desc_ts, "description"))
    else:
        desc_num = extract_numbered_tracklist(description)
        if len(desc_num) >= 4:
            candidates.append((desc_num, "description"))

    for idx, comment in enumerate(top_comments or []):
        c_ts = extract_timestamp_lines(comment, total_duration)
        if len(c_ts) >= 3:
            candidates.append((c_ts, f"comment#{idx + 1}"))
        else:
            c_num = extract_numbered_tracklist(comment)
            if len(c_num) >= 4:
                candidates.append((c_num, f"comment#{idx + 1}"))

    if not candidates:
        return [], ""
    # Prefer the candidate with the most tracks (tie-breaking by chapters > description > top comments).
    best_tracks, source = max(candidates, key=lambda pair: len(pair[0]))
    return best_tracks, source


def _norm_title_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _recover_missing_album_tracks(
    llm_tracks: list[Track],
    ref_tracks: list[Track],
    album_artist: str,
    album_title: str,
    uploader: str,
) -> list[Track]:
    """Ensure the LLM never drops tracks (e.g. the last track) that are present in chapters/description/comments."""
    if not ref_tracks:
        return llm_tracks
    if not llm_tracks:
        recovered: list[Track] = []
        for tr in ref_tracks:
            recovered.append(
                Track(
                    artist=tr.artist or album_artist,
                    title=tr.title,
                    album=album_title,
                    duration_sec=tr.duration_sec,
                    raw_title=tr.raw_title or tr.title,
                    uploader=uploader,
                )
            )
        return recovered

    if len(llm_tracks) >= len(ref_tracks):
        return llm_tracks

    llm_keys = {_norm_title_key(tr.title) for tr in llm_tracks}
    merged = list(llm_tracks)
    for ref in ref_tracks:
        rk = _norm_title_key(ref.title)
        if not rk:
            continue
        if any(rk == lk or rk in lk or lk in rk for lk in llm_keys if lk):
            continue
        merged.append(
            Track(
                artist=ref.artist or album_artist,
                title=ref.title,
                album=album_title,
                duration_sec=ref.duration_sec,
                raw_title=ref.raw_title or ref.title,
                uploader=uploader,
            )
        )
        llm_keys.add(rk)
    return merged


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


_MODEL_SIZE_B_RE = re.compile(r"(?:^|[:\-_])(\d+(?:\.\d+)?)b(?=$|[:\-_])", re.IGNORECASE)


def _estimate_model_billions(model_name: str) -> float | None:
    m = _MODEL_SIZE_B_RE.search(model_name or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


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

    def _fits_hardware(self, model_name: str) -> bool:
        hw = self.cfg.hardware
        if hw is None:
            return True
        size_b = _estimate_model_billions(model_name)
        if size_b is None:
            return hw.ram_gb >= 12
        return size_b <= float(hw.max_ollama_model_billions)

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
        eligible = [m for m in installed if self._fits_hardware(m)]
        if not eligible:
            return ""
        for preferred in _PREFERRED_MODELS:
            if preferred in eligible:
                return preferred
        return eligible[0]

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
        num_ctx = 2048 if (self.cfg.hardware and self.cfg.hardware.ram_gb <= 8) else 4096
        payload = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "keep_alive": keep_alive,
                "options": {"temperature": 0.1, "num_ctx": num_ctx, "num_predict": 1200},
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


def _mlx_is_available(cfg: PipelineConfig) -> bool:
    if os.environ.get("SLSK_DISABLE_MLX") == "1":
        return False
    if cfg.hardware and (not cfg.hardware.is_apple_silicon or cfg.hardware.ram_gb < 8):
        return False
    if not cfg.mlx_python or not cfg.mlx_base_model:
        return False
    worker = cfg.project_root / "src" / "mlx_worker.py"
    return (
        Path(cfg.mlx_python).is_file()
        and os.access(cfg.mlx_python, os.X_OK)
        and Path(cfg.mlx_base_model).is_dir()
        and worker.is_file()
    )


def _try_mlx_batch(
    cfg: PipelineConfig,
    requests: list[dict[str, str]],
    max_tokens: int = 1500,
) -> list[dict | None] | None:
    """Run one-shot MLX MiniCPM5-2B (+ LoRA adapter if present) inference and exit to keep 0 MB idle."""
    if not _mlx_is_available(cfg) or not requests:
        return None
    worker = cfg.project_root / "src" / "mlx_worker.py"
    adapter_dir = cfg.mlx_adapter_dir
    has_adapter = (adapter_dir / "adapters.safetensors").is_file()
    adapter_label = " + LoRA" if has_adapter else ""
    print(f"[LLM] Formattazione YouTube con MiniCPM5-2B-MLX{adapter_label} (Apple Silicon Metal)...", flush=True)

    payload = {
        "model": cfg.mlx_base_model,
        "adapter": str(adapter_dir) if has_adapter else "",
        "max_tokens": max_tokens,
        "requests": requests,
    }
    try:
        proc = subprocess.run(
            [cfg.mlx_python, str(worker)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=max(90, 60 * len(requests)),
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            print(f"[LLM] Avviso MLX: {err[-1] if err else proc.returncode} (provo fallback)")
            return None
        parsed_stdout = _parse_llm_json(proc.stdout)
        raw_outputs = parsed_stdout.get("outputs")
        if not isinstance(raw_outputs, list) or len(raw_outputs) != len(requests):
            return None
        results: list[dict | None] = []
        for raw_out in raw_outputs:
            try:
                results.append(_parse_llm_json(str(raw_out)))
            except Exception as exc:
                print(f"[LLM] Avviso parsing JSON MLX: {exc}")
                results.append(None)
        return results
    except Exception as exc:
        print(f"[LLM] Avviso MLX: {exc} (provo fallback)")
        return None


def _build_playlist_batch_prompts(
    playlist_title: str,
    playlist_uploader: str,
    is_ytm_album_hint: bool,
    raw_entries: list[dict],
    batch_size: int,
) -> tuple[list[list[dict]], list[dict[str, str]]]:
    chunks: list[list[dict]] = []
    requests: list[dict[str, str]] = []
    total_batches = (len(raw_entries) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        chunk = raw_entries[start : start + batch_size]
        chunks.append(chunk)

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
            )
        requests.append(
            {
                "system": PLAYLIST_SYSTEM_PROMPT,
                "user": classify_hint + "Items:\n" + "\n".join(lines),
            }
        )
    return chunks, requests


def format_youtube_playlist_with_llm(
    cfg: PipelineConfig,
    playlist_title: str,
    playlist_uploader: str,
    playlist_id: str,
    raw_entries: list[dict],
    source_url: str,
) -> CollectionExport:
    """Format a YouTube playlist (or YouTube album playlist) using fine-tuned MiniCPM5-2B MLX or Ollama."""
    is_ytm_album_hint = playlist_id.startswith("OLAK5uy_") or playlist_id.startswith("MPREb_")
    clean_title_guess = re.sub(r"^\s*Album\s*-\s*", "", playlist_title or "", flags=re.IGNORECASE).strip()

    is_album = False
    album_artist = ""
    album_title = ""
    formatted_tracks: list[Track] = []
    used_llm = False

    batch_size = cfg.llm_batch_size
    chunks, requests = _build_playlist_batch_prompts(
        playlist_title=playlist_title,
        playlist_uploader=playlist_uploader,
        is_ytm_album_hint=is_ytm_album_hint,
        raw_entries=raw_entries,
        batch_size=batch_size,
    )

    batch_responses: list[dict | None] | None = _try_mlx_batch(cfg, requests, max_tokens=1600)

    if batch_responses is None:
        with OllamaSession(cfg) as llm:
            if llm.available:
                print(f"[LLM] Formattazione playlist YouTube con Ollama ({llm.model})...")
                batch_responses = []
                total_batches = len(requests)
                for batch_idx, req in enumerate(requests):
                    if total_batches > 1:
                        print(f"[LLM] Batch {batch_idx + 1}/{total_batches}...", flush=True)
                    keep_alive: str | int = 0 if batch_idx == total_batches - 1 else "30s"
                    try:
                        batch_responses.append(llm.chat_json(req["system"], req["user"], keep_alive=keep_alive))
                    except Exception as exc:
                        print(f"[LLM] Avviso batch {batch_idx + 1}: {exc} (uso fallback locale)")
                        batch_responses.append(None)

    if batch_responses is not None:
        for batch_idx, (chunk, data) in enumerate(zip(chunks, batch_responses)):
            start = batch_idx * batch_size
            parsed_map: dict[int, tuple[str, str]] = {}
            if isinstance(data, dict):
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

            for offset, item in enumerate(chunk):
                idx = start + offset
                dur = item.get("duration")
                dur_sec = int(dur) if isinstance(dur, (int, float)) and dur > 0 else None
                raw_t = str(item.get("title") or "")
                uploader = str(item.get("channel") or "")
                if idx in parsed_map:
                    a, t = parsed_map[idx]
                    t = _GARBAGE_BRACKETS_RE.sub("", t).strip()
                    if not a and item.get("artist_tag"):
                        a = str(item["artist_tag"])
                    # Guardrail: if an LLM dropped the title part after ' - ' and returned only the artist as title
                    if not a and " - " in raw_t and t.lower() == raw_t.split(" - ", 1)[0].strip().lower():
                        heur = parse_track_heuristic(raw_t, uploader, default_artist=album_artist)
                        a, t = heur.artist, heur.title
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
        print("[YouTube] Formattazione tracce con parser locale integrato (LLM locale non rilevato).")
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
    top_comments = [str(c) for c in (meta.get("top_comments") or []) if str(c).strip()]
    yt_artist = str(meta.get("artist") or "").strip()
    yt_album = str(meta.get("album") or "").strip()
    yt_track = str(meta.get("track") or "").strip()

    ref_tracks, ref_source = _extract_best_reference_tracks(
        chapters=chapters,
        description=description,
        top_comments=top_comments,
        total_duration=dur_sec,
        uploader=uploader,
    )
    if ref_tracks and ref_source.startswith("comment#"):
        print(f"[YouTube] Tracklist trovata nei primi commenti ({len(ref_tracks)} tracce, {ref_source}).")

    has_album_signals = (
        bool(_ALBUM_KEYWORDS_RE.search(raw_title))
        or len(chapters) >= 3
        or len(ref_tracks) >= 3
        or (dur_sec is not None and dur_sec >= 900)
    )

    chapter_lines: list[str] = []
    for i, ch in enumerate(chapters):
        st = ch.get("start_time")
        en = ch.get("end_time")
        ch_dur = int(en - st) if isinstance(st, (int, float)) and isinstance(en, (int, float)) and en > st else None
        chapter_lines.append(f"{i}. {ch.get('title', '')} ({ch_dur or '?'}s)")

    desc_excerpt_lines = [ln for ln in description.splitlines() if ln.strip()]
    desc_excerpt = "\n".join(desc_excerpt_lines[:100])[:4500]

    comments_block = ""
    if top_comments:
        # Prioritize any comment that looks like a tracklist (timestamps or numbered lines), then top comments.
        scored_comments = sorted(
            top_comments,
            key=lambda c: (
                len(extract_timestamp_lines(c, dur_sec)) * 10 + len(extract_numbered_tracklist(c)) * 5,
                len(c),
            ),
            reverse=True,
        )
        chunks = [f"Comment {i + 1}:\n{c[:1800]}" for i, c in enumerate(scored_comments[:5])]
        comments_block = "\n\nTop comments:\n" + "\n---\n".join(chunks)[:4000]

    if not has_album_signals:
        sys_p = SINGLE_VIDEO_SYSTEM_PROMPT
        usr_p = (
            f"Video title: {raw_title}\n"
            f"Uploader: {uploader}\n"
            f"Metadata artist: {yt_artist}\n"
            f"Metadata track: {yt_track}\n"
            f"Description snippet: {desc_excerpt[:400]}"
        )
    else:
        sys_p = VIDEO_SYSTEM_PROMPT
        usr_p = (
            f"Video title: {raw_title}\n"
            f"Uploader: {uploader}\n"
            f"Total duration: {dur_sec or '?'}s\n"
            f"Metadata artist/album: {yt_artist} / {yt_album}\n"
            f"Chapters ({len(chapter_lines)}):\n" + "\n".join(chapter_lines[:60]) + "\n\n"
            f"Description excerpt:\n{desc_excerpt}"
            f"{comments_block}"
        )

    data: dict | None = None
    mlx_res = _try_mlx_batch(cfg, [{"system": sys_p, "user": usr_p}], max_tokens=2400)
    if mlx_res and isinstance(mlx_res[0], dict):
        data = mlx_res[0]
    else:
        with OllamaSession(cfg) as llm:
            if llm.available:
                print(f"[LLM] Analisi video YouTube con Ollama ({llm.model})...")
                try:
                    data = llm.chat_json(sys_p, usr_p, keep_alive=0)
                except Exception as exc:
                    print(f"[LLM] Avviso video YouTube: {exc} (uso fallback locale)")

    if isinstance(data, dict):
        if not has_album_signals:
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
        else:
            is_full_album = bool(data.get("is_full_album")) or len(ref_tracks) >= 3
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
                tracks = _recover_missing_album_tracks(tracks, ref_tracks, album_artist, album_title, uploader)
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

    # Deterministic fallback
    if has_album_signals:
        cleaned_title = _ALBUM_KEYWORDS_RE.sub("", raw_title)
        guess = parse_track_heuristic(cleaned_title, uploader, default_artist=yt_artist)
        album_artist = yt_artist or guess.artist
        album_title = yt_album or guess.title
        fallback_tracks = _recover_missing_album_tracks([], ref_tracks, album_artist, album_title, uploader)

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
