"""URL detection, extraction from highlighted text, redirect resolution, and filename sanitizing."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal
import urllib.parse
import urllib.request


UrlKind = Literal[
    "spotify_playlist",
    "spotify_album",
    "spotify_track",
    "youtube_playlist",
    "youtube_video",
]

_SUPPORTED_HOSTS = {
    "open.spotify.com",
    "spotify.link",
    "spoti.fi",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

_URL_CANDIDATE_RE = re.compile(
    r"(?:https?://[^\s<>\"'\]\)]+"
    r"|(?:(?:open\.spotify\.com|spotify\.link|spoti\.fi|(?:www\.|m\.|music\.)?youtube\.com|youtu\.be)/[^\s<>\"'\]\)]+)"
    r"|spotify:(?:playlist|album|track):[A-Za-z0-9]+)",
    re.IGNORECASE,
)

_SPOTIFY_URI_RE = re.compile(r"^spotify:(playlist|album|track):([A-Za-z0-9]+)$", re.IGNORECASE)
_SPOTIFY_PATH_RE = re.compile(r"/(?:intl-[a-z-]+/)?(playlist|album|track)/([A-Za-z0-9]+)", re.IGNORECASE)
_YT_VIDEO_PATH_RE = re.compile(r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{6,})", re.IGNORECASE)


@dataclass
class ParsedUrl:
    """Normalized URL classification result."""

    kind: UrlKind
    url: str
    item_id: str


def _ensure_scheme(url: str) -> str:
    lower = url.lower()
    if lower.startswith(("http://", "https://", "spotify:")):
        return url
    return "https://" + url


def extract_url_from_text(text: str) -> str | None:
    """Extract the first Spotify or YouTube URL/URI from highlighted text."""
    if not text:
        return None
    for match in _URL_CANDIDATE_RE.finditer(text.strip()):
        candidate = _ensure_scheme(match.group(0).rstrip(".,;:!?)"))
        if is_supported_domain(candidate):
            return candidate
    return None


def is_supported_domain(url: str) -> bool:
    """Return True if candidate looks like a Spotify or YouTube link/URI."""
    normalized = _ensure_scheme(url.strip())
    lower = normalized.lower()
    if lower.startswith("spotify:"):
        return bool(_SPOTIFY_URI_RE.match(normalized))
    parsed = urllib.parse.urlparse(lower)
    host = parsed.netloc.split(":")[0]
    return host in _SUPPORTED_HOSTS


def resolve_short_url(url: str, timeout: float = 8.0) -> str:
    """Follow redirects for shorteners such as spotify.link or spoti.fi."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if host not in {"spotify.link", "spoti.fi"}:
        return url

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl() or url
    except Exception:
        return url


def _is_youtube_radio_mix(list_id: str) -> bool:
    """Return True for personalized YouTube radio queues (RD...), excluding curated RDCLAK5uy_ playlists."""
    upper = list_id.upper()
    return upper.startswith("RD") and not upper.startswith("RDCLAK5UY_")


def classify_url(raw_input: str) -> ParsedUrl:
    """Resolve and classify a Spotify or YouTube URL into a concrete source type."""
    extracted = extract_url_from_text(raw_input) or raw_input.strip()
    if not extracted:
        raise ValueError("Nessun URL inserito.")

    resolved = resolve_short_url(_ensure_scheme(extracted))

    uri_match = _SPOTIFY_URI_RE.match(resolved)
    if uri_match:
        stype, sid = uri_match.group(1).lower(), uri_match.group(2)
        canonical = f"https://open.spotify.com/{stype}/{sid}"
        return ParsedUrl(kind=f"spotify_{stype}", url=canonical, item_id=sid)  # type: ignore[arg-type]

    parsed = urllib.parse.urlparse(resolved)
    host = parsed.netloc.lower().split(":")[0]

    if "spotify.com" in host:
        path_match = _SPOTIFY_PATH_RE.search(parsed.path)
        if not path_match:
            raise ValueError(f"Link Spotify non supportato (usa playlist, album o brano): {resolved}")
        stype, sid = path_match.group(1).lower(), path_match.group(2)
        canonical = f"https://open.spotify.com/{stype}/{sid}"
        return ParsedUrl(kind=f"spotify_{stype}", url=canonical, item_id=sid)  # type: ignore[arg-type]

    if host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }:
        # Unwrap YouTube attribution links (/attribution_link?u=%2Fwatch%3Fv%3D...)
        if parsed.path == "/attribution_link":
            qs_attr = urllib.parse.parse_qs(parsed.query)
            inner = (qs_attr.get("u") or [""])[0]
            if inner:
                return classify_url("https://www.youtube.com" + inner if inner.startswith("/") else inner)

        qs = urllib.parse.parse_qs(parsed.query)
        list_id = (qs.get("list") or [""])[0].strip()
        video_id = (qs.get("v") or [""])[0].strip()

        if host == "youtu.be":
            seg = parsed.path.strip("/").split("/")[0]
            if seg:
                video_id = seg

        if list_id and (not _is_youtube_radio_mix(list_id) or not video_id):
            base = "https://music.youtube.com" if host == "music.youtube.com" else "https://www.youtube.com"
            canonical = f"{base}/playlist?list={list_id}"
            return ParsedUrl(kind="youtube_playlist", url=canonical, item_id=list_id)

        if host == "music.youtube.com" and parsed.path.startswith("/browse/"):
            browse_id = parsed.path.strip("/").split("/")[-1]
            if browse_id:
                canonical = f"https://music.youtube.com/browse/{browse_id}"
                return ParsedUrl(kind="youtube_playlist", url=canonical, item_id=browse_id)

        if video_id:
            canonical = f"https://www.youtube.com/watch?v={video_id}"
            return ParsedUrl(kind="youtube_video", url=canonical, item_id=video_id)

        path_vid = _YT_VIDEO_PATH_RE.match(parsed.path)
        if path_vid:
            vid = path_vid.group(1)
            return ParsedUrl(
                kind="youtube_video",
                url=f"https://www.youtube.com/watch?v={vid}",
                item_id=vid,
            )

        raise ValueError(f"Link YouTube non riconosciuto (nessun video o playlist trovato): {resolved}")

    raise ValueError(f"URL non supportato (atteso link Spotify o YouTube): {resolved}")


def sanitize_filename(name: str, fallback: str = "playlist", max_len: int = 120) -> str:
    """Return a clean filesystem-safe folder/file name."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].strip(" .")
    return cleaned or fallback
