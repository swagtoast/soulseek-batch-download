"""Post-download cross-format automatic audio tagger & cover-art embedder.

Audits every downloaded audio track (.flac, .aiff, .mp3, .m4a, .wav) via ffprobe,
enriches missing/noisy metadata from Spotify, iTunes Search API, and MusicBrainz /
Cover Art Archive, normalizes tags with fine-tuned MiniCPM5-2B-MLX Tagger LoRA
(or deterministic scorer fallback on Intel/Windows), and writes metadata + cover art
losslessly via `ffmpeg -c:a copy`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

from config import PipelineConfig
from i18n import t
from llm_formatter import _mlx_is_available, _parse_llm_json
from models import CollectionExport, Track


TAGGABLE_EXTENSIONS = {".flac", ".aiff", ".aif", ".mp3", ".m4a", ".wav"}

TAGGER_SYSTEM_PROMPT = (
    "You audit and normalize audio file metadata for a music library (FLAC, AIFF, MP3).\n"
    "Given a batch of downloaded tracks with their current file tags, expected track info, "
    "and candidate releases from Spotify/iTunes/MusicBrainz, output clean, consistent metadata.\n"
    "Rules:\n"
    "- Ensure every track has clean: artist, album_artist, album, title, track_number (int or null), "
    "total_tracks (int or null), disc_number (int or null), date (YYYY or \"\").\n"
    "- Choose the best candidate release (`candidate_idx`, or -1 if none fits): prefer the original studio album/EP "
    "over 'Greatest Hits' / 'Best Of' / random 'Now That's What I Call Music' compilations unless the track is "
    "actually from that compilation. Match artist, title, and duration closely.\n"
    "- Strip Soulseek/rip junk from tags: '[FLAC]', '[24-bit]', '(Vinyl Rip)', '[WEB]', 'www.site.com', "
    "leading track numbers in title ('01. ', 'A1 - ').\n"
    "- Keep musically meaningful qualifiers: '(Original Mix)', '(... Remix)', '(Live)', '(feat. ...)'.\n"
    "- For `album_artist`: on a single-artist album, use the main album artist consistently across all tracks "
    "(do NOT include 'feat. ...' in album_artist). On multi-artist compilations, use 'Various Artists' (or the compilation curator).\n"
    "- Never invent a track_number or album if neither existing tags nor candidates provide it (use null or \"\").\n"
    "- Output strictly valid JSON matching:\n"
    '{"tracks": [{"idx": 0, "candidate_idx": 0, "artist": "...", "album_artist": "...", '
    '"album": "...", "title": "...", "track_number": 1, "total_tracks": 10, "disc_number": 1, "date": "1998"}]}.'
)

_JUNK_TAG_RE = re.compile(
    r"(?:"
    r"[\(\[]\s*(?:flac|24\s*-?\s*bit|16\s*-?\s*bit|vinyl\s*rip|cd\s*rip|web(?:\s*rip)?|"
    r"320\s*kbps|256\s*kbps|192\s*kbps|lossless|hq|hd|4k|official\s*(?:music\s*)?(?:video|audio)|"
    r"mp3\s*320|alac|aiff|wav|hi-?res)\s*[\)\]]"
    r"|\bwww\.[a-z0-9\-]+\.[a-z]{2,}\b(?:\s*[-–—|:]\s*)?"
    r")",
    re.IGNORECASE,
)
_LEADING_TRACK_NUM_RE = re.compile(r"^\s*(?:[A-Da-d]\d\s*[.\)\-:]?\s+|\d{1,3}\s*[.\)\-:]\s+)")
_ALBUM_YEAR_JUNK_RE = re.compile(r"\s*[\(\[]\s*(?:19\d{2}|20\d{2})\s*[\)\]]\s*$")
_FEAT_SPLIT_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
_COMPILATION_ALBUM_RE = re.compile(
    r"\b(?:greatest\s+hits|best\s+of|now\s+that'?s\s+what\s+i\s+call|"
    r"100\s+(?:greatest|best|hits)|ultimate\s+.+?\s+(?:hits|anthems|classics)|"
    r"club\s+anthems|lounge\s+compilation|hits\s+vol\.?\s*\d+|"
    r"essential\s+collection|anthology|top\s+\d+\s+hits|party\s+mix)\b",
    re.IGNORECASE,
)

_USER_AGENT = "SoulseekBatchPipeline/1.0 (https://github.com/swagtoast/soulseek-batch-download)"

_ITUNES_CACHE: dict[str, list[dict]] = {}
_MB_CACHE: dict[str, list[dict]] = {}
_COVER_CACHE: dict[str, bytes | None] = {}
_LAST_MB_CALL_TS: float = 0.0


@dataclass
class ProbedTags:
    path: Path
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track: str = ""
    track_number: int | None = None
    total_tracks: int | None = None
    disc: str = ""
    disc_number: int | None = None
    date: str = ""
    duration_sec: int | None = None
    has_cover: bool = False
    cover_codec: str | None = None


@dataclass
class TagDecision:
    artist: str
    album_artist: str
    album: str
    title: str
    track_number: int | None = None
    total_tracks: int | None = None
    disc_number: int | None = None
    date: str = ""
    cover_url: str = ""
    candidate_idx: int = -1


def _norm_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_only).strip()


def _parse_int_pair(raw: str) -> tuple[int | None, int | None]:
    s = (raw or "").strip()
    if not s:
        return (None, None)
    if "/" in s:
        left, right = s.split("/", 1)
        a = int(left.strip()) if left.strip().isdigit() else None
        b = int(right.strip()) if right.strip().isdigit() else None
        return (a if a and a > 0 else None, b if b and b > 0 else None)
    val = int(s) if s.isdigit() else None
    return (val if val and val > 0 else None, None)


def _parse_year(raw: str) -> str:
    m = re.search(r"\b(19\d{2}|20\d{2})\b", raw or "")
    return m.group(1) if m else ""


def _primary_artist(artist: str) -> str:
    cleaned = (artist or "").strip()
    if not cleaned:
        return ""
    if cleaned.lower() in {"various", "various artists", "va", "v.a.", "v/a"}:
        return "Various Artists"
    parts = _FEAT_SPLIT_RE.split(cleaned, maxsplit=1)
    return parts[0].strip() or cleaned


def clean_tag_value(value: str, is_title: bool = False, is_album: bool = False) -> str:
    """Strip Soulseek/rip junk from a tag string while preserving musical qualifiers."""
    s = (value or "").strip()
    if not s:
        return ""
    s = _JUNK_TAG_RE.sub("", s)
    if is_title:
        s = _LEADING_TRACK_NUM_RE.sub("", s)
    if is_album:
        s = _ALBUM_YEAR_JUNK_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -|")
    return s


def _has_junk_tag(value: str, is_title: bool = False) -> bool:
    s = (value or "").strip()
    if not s:
        return False
    if _JUNK_TAG_RE.search(s):
        return True
    if is_title and _LEADING_TRACK_NUM_RE.match(s):
        return True
    return False


def _coerce_positive_int(val: object) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    if isinstance(val, str):
        num, _ = _parse_int_pair(val)
        return num
    return None


def probe_audio_tags(ffprobe_bin: str, audio_path: Path) -> ProbedTags:
    """Read existing metadata, duration, and cover-art presence from any supported audio file."""
    probed = ProbedTags(path=audio_path)
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return probed
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return probed

    tags: dict[str, str] = {}
    fmt = info.get("format") or {}
    for k, v in (fmt.get("tags") or {}).items():
        if v is not None and str(v).strip():
            tags[str(k).lower()] = str(v).strip()

    for stream in info.get("streams") or []:
        ctype = stream.get("codec_type")
        if ctype == "audio":
            for k, v in (stream.get("tags") or {}).items():
                lk = str(k).lower()
                if lk not in tags and v is not None and str(v).strip():
                    tags[lk] = str(v).strip()
        elif ctype == "video":
            disp = stream.get("disposition") or {}
            if disp.get("attached_pic") == 1:
                probed.has_cover = True
                probed.cover_codec = str(stream.get("codec_name") or "mjpeg")

    dur_raw = fmt.get("duration")
    if dur_raw is not None:
        try:
            d = int(round(float(dur_raw)))
            if d > 0:
                probed.duration_sec = d
        except (TypeError, ValueError):
            pass

    probed.title = tags.get("title", "")
    probed.artist = tags.get("artist", "")
    probed.album = tags.get("album", "")
    probed.album_artist = tags.get("album_artist") or tags.get("albumartist") or tags.get("tpe2") or ""
    probed.date = _parse_year(tags.get("date") or tags.get("year") or tags.get("originaldate") or "")

    raw_trk = tags.get("track") or tags.get("tracknumber") or ""
    trk_n, trk_tot = _parse_int_pair(raw_trk)
    if trk_tot is None:
        tot_raw = tags.get("tracktotal") or tags.get("totaltracks") or ""
        trk_tot, _ = _parse_int_pair(tot_raw)
    probed.track_number = trk_n
    probed.total_tracks = trk_tot
    if trk_n is not None:
        probed.track = f"{trk_n}/{trk_tot}" if trk_tot else str(trk_n)

    raw_disc = tags.get("disc") or tags.get("discnumber") or ""
    disc_n, _ = _parse_int_pair(raw_disc)
    probed.disc_number = disc_n
    probed.disc = str(disc_n) if disc_n is not None else ""
    return probed


def tags_need_repair(probed: ProbedTags, require_cover: bool = True) -> bool:
    """Return True if any core tag or cover art is missing or contains Soulseek/rip junk."""
    if not (probed.artist and probed.album and probed.album_artist and probed.title):
        return True
    if probed.track_number is None or probed.track_number <= 0:
        return True
    if require_cover and probed.path.suffix.lower() != ".wav" and not probed.has_cover:
        return True
    if (
        _has_junk_tag(probed.title, is_title=True)
        or _has_junk_tag(probed.album)
        or _has_junk_tag(probed.artist)
        or _has_junk_tag(probed.album_artist)
    ):
        return True
    if _FEAT_SPLIT_RE.search(probed.album_artist):
        return True
    return False


def _filename_hint(path: Path) -> tuple[str, str, int | None]:
    """Best-effort parse of (artist, title, track_number) from a downloaded filename."""
    stem = path.stem.strip()
    trk_num: int | None = None
    m_num = re.match(r"^\s*(?:[A-Da-d])?(\d{1,3})\s*[.\)\-:]?\s+(.+)$", stem)
    if m_num:
        try:
            trk_num = int(m_num.group(1))
        except ValueError:
            trk_num = None
        stem = m_num.group(2).strip()

    stem = clean_tag_value(stem, is_title=False)
    for sep in (" - ", " – ", " — "):
        if sep in stem:
            a, t = stem.split(sep, 1)
            return (a.strip(), clean_tag_value(t, is_title=True), trk_num)
    return ("", clean_tag_value(stem, is_title=True), trk_num)


def _match_track_to_file_score(track: Track, probed: ProbedTags) -> float:
    title_norm = _norm_text(track.title)
    if not title_norm:
        return 0.0
    stem_norm = _norm_text(probed.path.stem)
    tag_title_norm = _norm_text(probed.title)
    best = 0.0
    for hay in (tag_title_norm, stem_norm):
        if not hay:
            continue
        if title_norm == hay:
            best = max(best, 1.0)
        elif title_norm in hay or (len(hay) >= 4 and hay in title_norm):
            best = max(best, 0.9)
        else:
            t_tokens = [tok for tok in title_norm.split() if len(tok) >= 2 or tok.isdigit()]
            if not t_tokens:
                t_tokens = title_norm.split()
            h_tokens = set(hay.split())
            if t_tokens and h_tokens:
                hits = sum(1 for tok in t_tokens if tok in h_tokens)
                best = max(best, hits / len(t_tokens))
    # Small bonus if artist also matches
    art_norm = _norm_text(track.artist)
    if best >= 0.5 and art_norm:
        file_art = _norm_text(probed.artist) or stem_norm
        if art_norm in file_art or any(tok in file_art for tok in art_norm.split() if len(tok) >= 3):
            best += 0.15
    return best


def _match_files_to_tracks(
    probed_files: list[ProbedTags],
    expected_tracks: list[Track],
) -> list[Track | None]:
    """1-to-1 greedy alignment between downloaded audio files and expected Track objects."""
    if not expected_tracks:
        return [None] * len(probed_files)

    pairs: list[tuple[float, int, int]] = []
    for fi, pf in enumerate(probed_files):
        for ti, tr in enumerate(expected_tracks):
            sc = _match_track_to_file_score(tr, pf)
            if sc >= 0.5:
                pairs.append((sc, fi, ti))
    pairs.sort(key=lambda item: item[0], reverse=True)

    assigned: list[Track | None] = [None] * len(probed_files)
    used_tracks: set[int] = set()
    for _sc, fi, ti in pairs:
        if assigned[fi] is not None or ti in used_tracks:
            continue
        assigned[fi] = expected_tracks[ti]
        used_tracks.add(ti)
    return assigned


def _http_get_json(url: str, timeout: float = 5.5) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _upgrade_itunes_artwork(url: str) -> str:
    if not url:
        return ""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/600x600bb.\1", url)


def search_itunes_candidates(
    artist: str,
    title: str,
    album: str = "",
    limit: int = 6,
) -> list[dict]:
    """Query Apple's public iTunes Search API (free, no key) for clean release candidates + cover art."""
    parts = [p.strip() for p in (artist, title, album) if p and p.strip()]
    if not parts:
        return []
    term = " ".join(parts)
    cache_key = term.lower()
    if cache_key in _ITUNES_CACHE:
        return list(_ITUNES_CACHE[cache_key])

    params = urllib.parse.urlencode(
        {"term": term, "media": "music", "entity": "song", "limit": str(limit)}
    )
    data = _http_get_json(f"https://itunes.apple.com/search?{params}", timeout=5.5)
    results: list[dict] = []
    if isinstance(data, dict):
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            tr_artist = str(item.get("artistName") or "").strip()
            tr_album = str(item.get("collectionName") or "").strip()
            tr_title = str(item.get("trackName") or "").strip()
            if not (tr_artist and tr_title):
                continue
            col_artist = str(item.get("collectionArtistName") or "").strip()
            if not col_artist:
                col_artist = _primary_artist(tr_artist)
            dur_ms = item.get("trackTimeMillis")
            dur_sec = int(round(int(dur_ms) / 1000)) if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None
            results.append(
                {
                    "src": "itunes",
                    "artist": tr_artist,
                    "album_artist": col_artist,
                    "album": tr_album,
                    "title": tr_title,
                    "track_number": _coerce_positive_int(item.get("trackNumber")),
                    "total_tracks": _coerce_positive_int(item.get("trackCount")),
                    "disc_number": _coerce_positive_int(item.get("discNumber")) or 1,
                    "date": _parse_year(str(item.get("releaseDate") or "")),
                    "duration_sec": dur_sec,
                    "cover_url": _upgrade_itunes_artwork(str(item.get("artworkUrl100") or "")),
                }
            )
    _ITUNES_CACHE[cache_key] = results
    return list(results)


def search_musicbrainz_candidates(
    artist: str,
    title: str,
    album: str = "",
    limit: int = 4,
) -> list[dict]:
    """Fallback query to MusicBrainz + Cover Art Archive when iTunes has no match."""
    global _LAST_MB_CALL_TS
    if not title.strip():
        return []
    q_parts = [f'recording:"{title.strip()}"']
    if artist.strip():
        q_parts.append(f'artist:"{artist.strip()}"')
    if album.strip():
        q_parts.append(f'release:"{album.strip()}"')
    query = " AND ".join(q_parts)
    cache_key = query.lower()
    if cache_key in _MB_CACHE:
        return list(_MB_CACHE[cache_key])

    # Respect MusicBrainz 1 req/sec rate limit gently.
    elapsed = time.monotonic() - _LAST_MB_CALL_TS
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)
    _LAST_MB_CALL_TS = time.monotonic()

    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": str(limit)})
    data = _http_get_json(f"https://musicbrainz.org/ws/2/recording/?{params}", timeout=6.0)
    results: list[dict] = []
    if isinstance(data, dict):
        for rec in data.get("recordings") or []:
            if not isinstance(rec, dict):
                continue
            rec_title = str(rec.get("title") or "").strip()
            credits = rec.get("artist-credit") or []
            rec_artist = "".join(
                str(c.get("name") or (c.get("artist") or {}).get("name") or "") + str(c.get("joinphrase") or "")
                for c in credits
                if isinstance(c, dict)
            ).strip()
            dur_ms = rec.get("length")
            dur_sec = int(round(int(dur_ms) / 1000)) if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None

            for rel in (rec.get("releases") or [])[:3]:
                if not isinstance(rel, dict):
                    continue
                rel_id = str(rel.get("id") or "").strip()
                rel_title = str(rel.get("title") or "").strip()
                if not (rec_title and rec_artist and rel_title):
                    continue
                rg = rel.get("release-group") or {}
                sec_types = {str(s).lower() for s in (rg.get("secondary-types") or [])}
                is_comp = "compilation" in sec_types

                media = rel.get("media") or []
                trk_num: int | None = None
                trk_tot: int | None = None
                disc_num: int = 1
                if media and isinstance(media[0], dict):
                    m0 = media[0]
                    trk_tot = _coerce_positive_int(m0.get("track-count"))
                    disc_num = _coerce_positive_int(m0.get("position")) or 1
                    tracks_m = m0.get("track") or []
                    if tracks_m and isinstance(tracks_m[0], dict):
                        trk_num = _coerce_positive_int(tracks_m[0].get("number"))

                aa = "Various Artists" if is_comp else _primary_artist(rec_artist)
                results.append(
                    {
                        "src": "musicbrainz",
                        "artist": rec_artist,
                        "album_artist": aa,
                        "album": rel_title,
                        "title": rec_title,
                        "track_number": trk_num,
                        "total_tracks": trk_tot,
                        "disc_number": disc_num,
                        "date": _parse_year(str(rel.get("date") or "")),
                        "duration_sec": dur_sec,
                        "cover_url": f"https://coverartarchive.org/release/{rel_id}/front-500" if rel_id else "",
                    }
                )
    _MB_CACHE[cache_key] = results
    return list(results)


def download_cover_art(url: str) -> bytes | None:
    """Download JPEG/PNG cover art bytes (with session cache and magic-byte validation)."""
    if not url:
        return None
    if url in _COVER_CACHE:
        return _COVER_CACHE[url]

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=6.5) as resp:
            data = resp.read(8 * 1024 * 1024)  # Cap at 8 MB
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        _COVER_CACHE[url] = None
        return None

    is_jpeg = len(data) > 16 and data[:3] == b"\xff\xd8\xff"
    is_png = len(data) > 16 and data[:8] == b"\x89PNG\r\n\x1a\n"
    if not (is_jpeg or is_png):
        _COVER_CACHE[url] = None
        return None

    _COVER_CACHE[url] = data
    return data


def _token_overlap(a: str, b: str) -> float:
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def score_candidate(
    cand: dict,
    expected_artist: str,
    expected_title: str,
    expected_album: str,
    duration_sec: int | None,
    export_kind: str,
) -> float:
    """Score a release candidate, preferring original studio albums over random compilations."""
    score = 0.0
    title_sim = _token_overlap(expected_title, str(cand.get("title") or ""))
    artist_sim = _token_overlap(expected_artist, str(cand.get("artist") or ""))
    if title_sim < 0.35:
        return -100.0
    score += title_sim * 45.0
    if expected_artist:
        if artist_sim < 0.25:
            return -80.0
        score += artist_sim * 35.0
    else:
        score += 10.0

    cand_album = str(cand.get("album") or "")
    cand_aa = str(cand.get("album_artist") or "")
    if expected_album:
        alb_sim = _token_overlap(expected_album, cand_album)
        score += alb_sim * 22.0

    # Penalize Greatest Hits / Best Of / Now That's What I Call compilations unless expected album matches
    expected_is_comp = bool(_COMPILATION_ALBUM_RE.search(expected_album or "")) or (
        _norm_text(expected_artist) == "various artists"
    )
    cand_is_comp = bool(_COMPILATION_ALBUM_RE.search(cand_album)) or (
        _norm_text(cand_aa) in {"various artists", "various"}
    )
    if cand_is_comp and not expected_is_comp:
        score -= 35.0

    # Duration match
    cand_dur = cand.get("duration_sec")
    if duration_sec and isinstance(cand_dur, (int, float)) and cand_dur > 0:
        diff = abs(duration_sec - int(cand_dur))
        if diff <= 5:
            score += 15.0
        elif diff <= 15:
            score += 7.0
        elif diff >= 45:
            score -= 20.0

    if cand.get("src") == "spotify":
        score += 8.0
    if cand.get("cover_url"):
        score += 4.0
    if cand.get("track_number"):
        score += 3.0
    if export_kind == "album" and expected_album and _token_overlap(expected_album, cand_album) >= 0.8:
        score += 12.0
    return score


def _choose_candidate_deterministic(
    cands: list[dict],
    expected_artist: str,
    expected_title: str,
    expected_album: str,
    duration_sec: int | None,
    export_kind: str,
) -> int:
    best_idx = -1
    best_score = 35.0  # Minimum bar so unrelated hits are rejected
    for idx, c in enumerate(cands):
        sc = score_candidate(c, expected_artist, expected_title, expected_album, duration_sec, export_kind)
        if sc > best_score:
            best_score = sc
            best_idx = idx
    return best_idx


def _gather_candidates_for_item(
    probed: ProbedTags,
    expected_track: Track | None,
    export: CollectionExport,
    allow_network: bool = True,
) -> tuple[list[dict], str, str, str]:
    """Build candidate release list from Spotify metadata, iTunes Search API, and MusicBrainz."""
    fn_artist, fn_title, _fn_trk = _filename_hint(probed.path)
    exp_artist = (
        (expected_track.artist if expected_track and expected_track.artist else "")
        or clean_tag_value(probed.artist)
        or (export.artist if export.kind == "album" else "")
        or fn_artist
    )
    exp_title = (
        (expected_track.title if expected_track and expected_track.title else "")
        or clean_tag_value(probed.title, is_title=True)
        or fn_title
    )
    exp_album = (
        (expected_track.album if expected_track and expected_track.album else "")
        or (export.album if export.kind == "album" and export.album else "")
        or clean_tag_value(probed.album, is_album=True)
    )

    cands: list[dict] = []

    # 1) Direct Spotify ground-truth metadata (zero extra HTTP calls!)
    if expected_track and export.source_platform == "spotify":
        sp_album = expected_track.album or (export.album if export.kind == "album" else "")
        sp_aa = (
            expected_track.album_artist
            or (export.artist if export.kind == "album" else "")
            or _primary_artist(expected_track.artist)
        )
        sp_cover = expected_track.cover_url or (export.cover_url if export.kind == "album" else "")
        if sp_album and (expected_track.artist or sp_aa):
            cands.append(
                {
                    "src": "spotify",
                    "artist": expected_track.artist or sp_aa,
                    "album_artist": sp_aa,
                    "album": sp_album,
                    "title": expected_track.title or exp_title,
                    "track_number": expected_track.track_number,
                    "total_tracks": expected_track.total_tracks or (export.total_tracks if export.kind == "album" else None),
                    "disc_number": expected_track.disc_number or 1,
                    "date": expected_track.release_year,
                    "duration_sec": expected_track.duration_sec or probed.duration_sec,
                    "cover_url": sp_cover,
                }
            )

    # 2) iTunes Search API (if we don't already have a complete Spotify candidate with cover + track number)
    has_complete_spotify = bool(
        cands
        and cands[0].get("album")
        and cands[0].get("album_artist")
        and cands[0].get("track_number")
        and cands[0].get("cover_url")
    )
    if allow_network and not has_complete_spotify and exp_title:
        it_album_hint = exp_album if export.kind == "album" else ""
        it_cands = search_itunes_candidates(exp_artist, exp_title, it_album_hint, limit=5)
        if not it_cands and it_album_hint:
            it_cands = search_itunes_candidates(exp_artist, exp_title, "", limit=5)
        cands.extend(it_cands)

        # 3) MusicBrainz + Cover Art Archive fallback only if iTunes didn't find a strong match
        best_it = _choose_candidate_deterministic(
            cands, exp_artist, exp_title, exp_album, probed.duration_sec, export.kind
        )
        if best_it == -1:
            mb_cands = search_musicbrainz_candidates(exp_artist, exp_title, it_album_hint, limit=4)
            cands.extend(mb_cands)

    return (cands[:6], exp_artist, exp_title, exp_album)


def _try_mlx_tagger_batch(
    cfg: PipelineConfig,
    requests: list[dict[str, str]],
    max_tokens: int = 1600,
) -> list[dict | None] | None:
    """Invoke one-shot MiniCPM5-2B-MLX + Tagger LoRA via mlx_worker.py (0 MB idle RAM)."""
    if not _mlx_is_available(cfg) or not requests:
        return None
    worker = cfg.project_root / "src" / "mlx_worker.py"
    adapter_dir = cfg.mlx_tagger_adapter_dir
    has_adapter = (adapter_dir / "adapters.safetensors").is_file()
    if not has_adapter:
        return None

    print(t("tag_mlx_start"), flush=True)
    payload = {
        "model": cfg.mlx_base_model,
        "adapter": str(adapter_dir),
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
            print(t("tag_mlx_warn", err=err[-1] if err else proc.returncode))
            return None
        parsed_stdout = _parse_llm_json(proc.stdout)
        raw_outputs = parsed_stdout.get("outputs")
        if not isinstance(raw_outputs, list) or len(raw_outputs) != len(requests):
            return None
        results: list[dict | None] = []
        for raw_out in raw_outputs:
            try:
                results.append(_parse_llm_json(str(raw_out)))
            except Exception:
                results.append(None)
        return results
    except Exception as exc:
        print(t("tag_mlx_warn", err=exc))
        return None


def _build_tagger_prompt(
    items: list[dict],
    export: CollectionExport,
) -> str:
    if export.kind == "album":
        tot = export.total_tracks or len(export.tracks) or len(items)
        header = (
            f"Collection kind: album\n"
            f"Expected album: {export.artist or ''} - {export.album or export.name} ({tot} tracks)\n"
        )
    else:
        header = "Collection kind: playlist (tag each track with its real original album)\n"

    lines: list[str] = []
    for idx, it in enumerate(items):
        probed: ProbedTags = it["probed"]
        cands: list[dict] = it["candidates"]
        cand_strs = []
        for ci, c in enumerate(cands):
            cand_strs.append(
                f"({ci}) [{c.get('src', '')}] artist={c.get('artist', '')!r} aa={c.get('album_artist', '')!r} "
                f"album={c.get('album', '')!r} title={c.get('title', '')!r} "
                f"trk={c.get('track_number')}/{c.get('total_tracks')} "
                f"disc={c.get('disc_number') or 1} date={c.get('date', '')!r} dur={c.get('duration_sec') or '?'}s"
            )
        lines.append(
            f"Track {idx} | file={probed.path.name!r} | dur={probed.duration_sec or '?'}s\n"
            f"  current_tags: artist={probed.artist!r} aa={probed.album_artist!r} album={probed.album!r} "
            f"title={probed.title!r} trk={probed.track!r} disc={probed.disc!r} date={probed.date!r} cover={probed.has_cover}\n"
            f"  expected_hint: artist={it['exp_artist']!r} title={it['exp_title']!r}\n"
            f"  candidates: " + (" ; ".join(cand_strs) if cand_strs else "none")
        )
    return header + "\n".join(lines)


def _decision_from_deterministic(item: dict, export: CollectionExport) -> TagDecision:
    probed: ProbedTags = item["probed"]
    cands: list[dict] = item["candidates"]
    expected_track: Track | None = item["expected_track"]
    exp_artist: str = item["exp_artist"]
    exp_title: str = item["exp_title"]
    exp_album: str = item["exp_album"]

    ci = _choose_candidate_deterministic(
        cands, exp_artist, exp_title, exp_album, probed.duration_sec, export.kind
    )
    if 0 <= ci < len(cands):
        c = cands[ci]
        artist = clean_tag_value(str(c.get("artist") or exp_artist))
        aa = clean_tag_value(str(c.get("album_artist") or _primary_artist(artist)))
        album = clean_tag_value(str(c.get("album") or exp_album), is_album=True)
        title = clean_tag_value(str(c.get("title") or exp_title), is_title=True)
        trk_n = _coerce_positive_int(c.get("track_number")) or probed.track_number
        trk_tot = _coerce_positive_int(c.get("total_tracks")) or probed.total_tracks
        disc_n = _coerce_positive_int(c.get("disc_number")) or probed.disc_number or 1
        date = _parse_year(str(c.get("date") or "")) or probed.date
        cover_url = str(c.get("cover_url") or "")
        return TagDecision(
            artist=artist,
            album_artist=aa,
            album=album,
            title=title,
            track_number=trk_n,
            total_tracks=trk_tot,
            disc_number=disc_n,
            date=date,
            cover_url=cover_url,
            candidate_idx=ci,
        )

    # Fallback: clean existing tags + expected hints + filename track number
    _fn_a, _fn_t, fn_trk = _filename_hint(probed.path)
    artist = clean_tag_value(probed.artist) or exp_artist
    aa = _primary_artist(clean_tag_value(probed.album_artist) or (export.artist if export.kind == "album" else "") or artist)
    album = clean_tag_value(probed.album, is_album=True) or exp_album
    title = clean_tag_value(probed.title, is_title=True) or exp_title
    trk_n = (
        probed.track_number
        or (expected_track.track_number if expected_track else None)
        or fn_trk
    )
    trk_tot = (
        probed.total_tracks
        or (expected_track.total_tracks if expected_track else None)
        or (export.total_tracks if export.kind == "album" else None)
    )
    disc_n = probed.disc_number or (expected_track.disc_number if expected_track else None) or 1
    date = probed.date or (expected_track.release_year if expected_track else "")
    cover_url = (
        (expected_track.cover_url if expected_track and expected_track.cover_url else "")
        or (export.cover_url if export.kind == "album" else "")
    )
    return TagDecision(
        artist=artist,
        album_artist=aa,
        album=album,
        title=title,
        track_number=trk_n,
        total_tracks=trk_tot,
        disc_number=disc_n,
        date=date,
        cover_url=cover_url,
        candidate_idx=-1,
    )


def _decision_from_llm_row(row: dict, item: dict, fallback: TagDecision) -> TagDecision:
    """Merge and sanitize an LLM-produced tag decision with deterministic safeguards."""
    cands: list[dict] = item["candidates"]
    raw_ci = row.get("candidate_idx")
    ci = int(raw_ci) if isinstance(raw_ci, int) and 0 <= raw_ci < len(cands) else -1
    chosen_cand = cands[ci] if ci >= 0 else None

    artist = clean_tag_value(str(row.get("artist") or "")) or fallback.artist
    aa_raw = clean_tag_value(str(row.get("album_artist") or ""))
    aa = _primary_artist(aa_raw) if aa_raw else fallback.album_artist
    album = clean_tag_value(str(row.get("album") or ""), is_album=True) or fallback.album
    title = clean_tag_value(str(row.get("title") or ""), is_title=True) or fallback.title
    trk_n = _coerce_positive_int(row.get("track_number")) or fallback.track_number
    trk_tot = _coerce_positive_int(row.get("total_tracks")) or fallback.total_tracks
    if trk_n and trk_tot and trk_tot < trk_n:
        trk_tot = trk_n
    disc_n = _coerce_positive_int(row.get("disc_number")) or fallback.disc_number or 1
    date = _parse_year(str(row.get("date") or "")) or fallback.date
    cover_url = str((chosen_cand or {}).get("cover_url") or "") or fallback.cover_url

    return TagDecision(
        artist=artist,
        album_artist=aa,
        album=album,
        title=title,
        track_number=trk_n,
        total_tracks=trk_tot,
        disc_number=disc_n,
        date=date,
        cover_url=cover_url,
        candidate_idx=ci,
    )


def _enforce_album_cohesion(decisions: list[TagDecision], export: CollectionExport) -> None:
    """For Full Albums, guarantee consistent album_artist, total_tracks, and cover_url across every track."""
    if export.kind != "album" or len(decisions) < 2:
        return

    aa_counts: dict[str, int] = {}
    for d in decisions:
        if d.album_artist:
            aa_counts[d.album_artist] = aa_counts.get(d.album_artist, 0) + 1
    majority_aa = max(aa_counts.items(), key=lambda kv: kv[1])[0] if aa_counts else ""
    canonical_aa = _primary_artist(majority_aa or export.artist)

    alb_counts: dict[str, int] = {}
    for d in decisions:
        if d.album:
            alb_counts[d.album] = alb_counts.get(d.album, 0) + 1
    majority_album = max(alb_counts.items(), key=lambda kv: kv[1])[0] if alb_counts else export.album

    max_track = max((d.track_number or 0) for d in decisions)
    canonical_total = max(
        export.total_tracks or 0,
        len(decisions),
        max_track,
        max((d.total_tracks or 0) for d in decisions),
    )
    shared_cover = next((d.cover_url for d in decisions if d.cover_url), export.cover_url)
    shared_date = next((d.date for d in decisions if d.date), "")

    for d in decisions:
        if canonical_aa:
            d.album_artist = canonical_aa
        if not d.album and majority_album:
            d.album = majority_album
        if d.track_number and canonical_total >= d.track_number:
            d.total_tracks = canonical_total
        if not d.cover_url and shared_cover:
            d.cover_url = shared_cover
        if not d.date and shared_date:
            d.date = shared_date


def _decision_changes_file(probed: ProbedTags, dec: TagDecision, will_embed_cover: bool) -> bool:
    if will_embed_cover:
        return True
    if dec.artist and dec.artist != probed.artist:
        return True
    if dec.album_artist and dec.album_artist != probed.album_artist:
        return True
    if dec.album and dec.album != probed.album:
        return True
    if dec.title and dec.title != probed.title:
        return True
    if dec.track_number and dec.track_number != probed.track_number:
        return True
    if dec.total_tracks and dec.total_tracks != probed.total_tracks:
        return True
    if dec.disc_number and dec.disc_number != probed.disc_number:
        return True
    if dec.date and dec.date != probed.date:
        return True
    return False


def _verify_tagged_audio(ffprobe_bin: str, target: Path) -> bool:
    if not target.is_file() or target.stat().st_size <= 0:
        return False
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
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
    return any(s.get("codec_type") == "audio" for s in (info.get("streams") or []))


def _build_tag_ffmpeg_args(
    ffmpeg_bin: str,
    src: Path,
    temp_out: Path,
    dec: TagDecision,
    cover_file: Path | None,
    existing_cover_codec: str | None,
) -> list[str]:
    ext = src.suffix.lower()
    args = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
    ]
    if cover_file is not None and ext != ".wav":
        args.extend(["-i", str(cover_file)])

    args.extend(["-map", "0:a:0"])
    if ext == ".wav":
        args.append("-vn")
    elif cover_file is not None:
        args.extend(
            [
                "-map",
                "1:v:0",
                "-c:v",
                "copy",
                "-disposition:v:0",
                "attached_pic",
                "-metadata:s:v",
                "title=Album cover",
                "-metadata:s:v",
                "comment=Cover (front)",
            ]
        )
    elif existing_cover_codec:
        args.extend(
            [
                "-map",
                "0:v:0",
                "-c:v",
                "copy" if existing_cover_codec in {"mjpeg", "png"} else "mjpeg",
                "-disposition:v:0",
                "attached_pic",
            ]
        )
    else:
        args.append("-vn")

    args.extend(["-map_metadata", "0", "-c:a", "copy"])

    # Standard cross-format metadata fields
    def add_meta(key: str, val: str) -> None:
        if val:
            args.extend(["-metadata", f"{key}={val}"])

    add_meta("title", dec.title)
    add_meta("artist", dec.artist)
    add_meta("album", dec.album)
    add_meta("album_artist", dec.album_artist)
    if ext == ".flac":
        if dec.track_number is not None:
            add_meta("track", str(dec.track_number))
            if dec.total_tracks:
                add_meta("tracktotal", str(dec.total_tracks))
        if dec.disc_number is not None:
            add_meta("disc", str(dec.disc_number))
    else:
        if dec.track_number is not None:
            trk_str = f"{dec.track_number}/{dec.total_tracks}" if dec.total_tracks else str(dec.track_number)
            add_meta("track", trk_str)
        if dec.disc_number is not None:
            add_meta("disc", str(dec.disc_number))
    add_meta("date", dec.date)

    # Container-specific output flags
    if ext == ".mp3":
        args.extend(["-id3v2_version", "3", "-write_id3v1", "1", "-f", "mp3"])
    elif ext in {".aiff", ".aif"}:
        args.extend(["-write_id3v2", "1", "-id3v2_version", "3", "-f", "aiff"])
    elif ext == ".flac":
        args.extend(["-f", "flac"])
    elif ext == ".m4a":
        args.extend(["-f", "ipod"])
    elif ext == ".wav":
        args.extend(["-write_id3v2", "1", "-id3v2_version", "3", "-f", "wav"])

    args.append(str(temp_out))
    return args


def write_audio_tags_lossless(
    ffmpeg_bin: str,
    ffprobe_bin: str,
    audio_path: Path,
    dec: TagDecision,
    cover_bytes: bytes | None = None,
    existing_cover_codec: str | None = None,
) -> bool:
    """Losslessly write metadata + optional cover art via `ffmpeg -c:a copy` and atomic swap."""
    ext = audio_path.suffix.lower()
    if ext not in TAGGABLE_EXTENSIONS or not audio_path.is_file():
        return False

    temp_out = audio_path.with_name(f".{audio_path.stem}.tagging-{uuid.uuid4().hex[:8]}{ext}")
    cover_tmp: Path | None = None
    try:
        if cover_bytes and ext != ".wav":
            cover_ext = ".png" if cover_bytes[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
            fd, raw_name = tempfile.mkstemp(prefix="slsk_cover_", suffix=cover_ext)
            os.close(fd)
            cover_tmp = Path(raw_name)
            cover_tmp.write_bytes(cover_bytes)

        cmd = _build_tag_ffmpeg_args(
            ffmpeg_bin=ffmpeg_bin,
            src=audio_path,
            temp_out=temp_out,
            dec=dec,
            cover_file=cover_tmp,
            existing_cover_codec=existing_cover_codec,
        )
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)

        # If muxing cover art failed on an unusual source stream, retry text tags only (never lose audio).
        if (proc.returncode != 0 or not _verify_tagged_audio(ffprobe_bin, temp_out)) and cover_tmp is not None:
            temp_out.unlink(missing_ok=True)
            cmd_retry = _build_tag_ffmpeg_args(
                ffmpeg_bin=ffmpeg_bin,
                src=audio_path,
                temp_out=temp_out,
                dec=dec,
                cover_file=None,
                existing_cover_codec=existing_cover_codec,
            )
            proc = subprocess.run(cmd_retry, capture_output=True, text=True, check=False)

        if proc.returncode != 0 or not _verify_tagged_audio(ffprobe_bin, temp_out):
            return False

        shutil.move(str(temp_out), str(audio_path))
        return True
    except OSError:
        return False
    finally:
        temp_out.unlink(missing_ok=True)
        if cover_tmp is not None:
            cover_tmp.unlink(missing_ok=True)


def tag_downloaded_files(
    downloaded_audio: list[Path],
    export: CollectionExport,
    cfg: PipelineConfig,
    allow_network: bool = True,
) -> tuple[int, int, int]:
    """Audit and repair tags + cover art on all downloaded audio files.

    Returns (updated_count, already_ok_count, failed_count).
    """
    audio_files = [p for p in downloaded_audio if p.is_file() and p.suffix.lower() in TAGGABLE_EXTENSIONS]
    if not audio_files:
        return (0, 0, 0)

    print(t("tag_start", count=len(audio_files)))

    probed_list = [probe_audio_tags(cfg.ffprobe_bin, p) for p in audio_files]
    matched_tracks = _match_files_to_tracks(probed_list, export.tracks)

    # Check if every single file is already clean and complete (and cohesive for albums).
    needs_any = [tags_need_repair(p) for p in probed_list]
    if export.kind == "album" and len(probed_list) >= 2:
        aas = {p.album_artist for p in probed_list if p.album_artist}
        if len(aas) != 1:
            needs_any = [True] * len(probed_list)

    if not any(needs_any):
        print(t("tag_all_ok", count=len(audio_files)))
        return (0, len(audio_files), 0)

    # Gather ground-truth candidates only for tracks that need repair (or album cohesion).
    items: list[dict] = []
    for pf, tr, need in zip(probed_list, matched_tracks, needs_any):
        if need:
            cands, exp_a, exp_t, exp_alb = _gather_candidates_for_item(
                pf, tr, export, allow_network=allow_network
            )
        else:
            cands, exp_a, exp_t, exp_alb = ([], pf.artist, pf.title, pf.album)
        items.append(
            {
                "probed": pf,
                "expected_track": tr,
                "needs_repair": need,
                "candidates": cands,
                "exp_artist": exp_a,
                "exp_title": exp_t,
                "exp_album": exp_alb,
            }
        )

    # Compute deterministic baseline decisions first (always safe & fast).
    decisions = [_decision_from_deterministic(it, export) for it in items]

    # Tier 1: Fine-tuned MiniCPM5-2B-MLX Tagger LoRA for items needing repair (chunked by 4 to fit <2048 ctx).
    repair_indices = [i for i, it in enumerate(items) if it["needs_repair"]]
    if repair_indices:
        chunk_size = 4
        chunks: list[list[int]] = [
            repair_indices[i : i + chunk_size] for i in range(0, len(repair_indices), chunk_size)
        ]
        mlx_requests = [
            {"system": TAGGER_SYSTEM_PROMPT, "user": _build_tagger_prompt([items[idx] for idx in ch], export)}
            for ch in chunks
        ]
        mlx_results = _try_mlx_tagger_batch(cfg, mlx_requests)
        if mlx_results is not None:
            for ch, res in zip(chunks, mlx_results):
                if not isinstance(res, dict):
                    continue
                rows = res.get("tracks")
                if not isinstance(rows, list):
                    continue
                by_local_idx = {
                    int(r["idx"]): r
                    for r in rows
                    if isinstance(r, dict) and isinstance(r.get("idx"), int)
                }
                for local_i, global_i in enumerate(ch):
                    row = by_local_idx.get(local_i)
                    if row is not None:
                        decisions[global_i] = _decision_from_llm_row(row, items[global_i], decisions[global_i])

    # Enforce album cohesion across all tracks when downloading a Full Album.
    _enforce_album_cohesion(decisions, export)

    updated = 0
    already_ok = 0
    failed = 0

    for pf, dec in zip(probed_list, decisions):
        need_cover = (pf.path.suffix.lower() != ".wav") and (not pf.has_cover) and bool(dec.cover_url)
        cover_bytes: bytes | None = None
        if need_cover and allow_network:
            cover_bytes = download_cover_art(dec.cover_url)

        will_embed_cover = cover_bytes is not None
        if not _decision_changes_file(pf, dec, will_embed_cover):
            already_ok += 1
            continue

        ok = write_audio_tags_lossless(
            ffmpeg_bin=cfg.ffmpeg_bin,
            ffprobe_bin=cfg.ffprobe_bin,
            audio_path=pf.path,
            dec=dec,
            cover_bytes=cover_bytes,
            existing_cover_codec=pf.cover_codec,
        )
        if ok:
            updated += 1
            cov_note = t("tag_cover_note") if will_embed_cover else ""
            trk_note = f" #{dec.track_number}/{dec.total_tracks}" if (dec.track_number and dec.total_tracks) else (
                f" #{dec.track_number}" if dec.track_number else ""
            )
            print(f"  [OK] {pf.path.name} -> {dec.artist} - {dec.title} [{dec.album}{trk_note}]{cov_note}")
        else:
            failed += 1
            print(t("tag_write_fail", name=pf.path.name))

    err_part = t("tag_summary_err", failed=failed) if failed else ""
    print(t("tag_summary", total=len(audio_files), updated=updated, ok=already_ok, err_part=err_part))
    return (updated, already_ok, failed)
