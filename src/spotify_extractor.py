"""Pure-stdlib Spotify playlist, album, and track extractor with Embed fallback."""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from models import CollectionExport, Track
from url_utils import ParsedUrl, sanitize_filename


_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class SpotifyExtractionError(RuntimeError):
    """Raised when both Spotify API and Embed fallback fail."""


def _http_json(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_access_token(client_id: str, client_secret: str) -> str:
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    payload = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
    data = _http_json(
        "https://accounts.spotify.com/api/token",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=payload,
    )
    token = data.get("access_token")
    if not token:
        raise SpotifyExtractionError("Token Spotify non ricevuto.")
    return str(token)


def _clean_text(text: str) -> str:
    return html.unescape(text or "").replace("\xa0", " ").strip()


def _track_from_api_obj(track_obj: dict, fallback_album: str = "") -> Track | None:
    if not isinstance(track_obj, dict):
        return None
    title = _clean_text(track_obj.get("name") or "")
    artists = track_obj.get("artists") or []
    artist = ""
    if artists and isinstance(artists[0], dict):
        artist = _clean_text(artists[0].get("name") or "")
    if not title or not artist:
        return None

    album_name = fallback_album
    album_obj = track_obj.get("album")
    if isinstance(album_obj, dict) and album_obj.get("name"):
        album_name = _clean_text(album_obj["name"])

    dur_ms = track_obj.get("duration_ms")
    dur_sec = int(dur_ms // 1000) if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None
    return Track(artist=artist, title=title, album=album_name, duration_sec=dur_sec)


def _extract_via_api(parsed: ParsedUrl, client_id: str, client_secret: str) -> CollectionExport:
    token = _get_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    if parsed.kind == "spotify_playlist":
        fields = "name,tracks(items(track(name,duration_ms,artists(name),album(name))),next,total)"
        url = f"https://api.spotify.com/v1/playlists/{parsed.item_id}?fields={urllib.parse.quote(fields, safe=',()')}"
        data = _http_json(url, headers=headers)
        name = _clean_text(data.get("name") or "") or f"Spotify Playlist {parsed.item_id}"
        page = data.get("tracks") or {}
        items = list(page.get("items") or [])
        next_url = page.get("next")
        while next_url:
            page = _http_json(next_url, headers=headers)
            items.extend(page.get("items") or [])
            next_url = page.get("next")

        tracks: list[Track] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            t = _track_from_api_obj(entry.get("track") or {})
            if t:
                tracks.append(t)

        return CollectionExport(
            kind="playlist",
            name=sanitize_filename(name, fallback="spotify_playlist"),
            tracks=tracks,
            total_tracks=len(tracks),
            source_url=parsed.url,
            source_platform="spotify",
        )

    if parsed.kind == "spotify_album":
        url = f"https://api.spotify.com/v1/albums/{parsed.item_id}"
        data = _http_json(url, headers=headers)
        album_title = _clean_text(data.get("name") or "") or f"Album {parsed.item_id}"
        artists = data.get("artists") or []
        album_artist = _clean_text(artists[0].get("name") or "") if artists else ""
        total_tracks = int(data.get("total_tracks") or 0) or None

        page = data.get("tracks") or {}
        items = list(page.get("items") or [])
        next_url = page.get("next")
        while next_url:
            page = _http_json(next_url, headers=headers)
            items.extend(page.get("items") or [])
            next_url = page.get("next")

        tracks = []
        for item in items:
            t = _track_from_api_obj(item, fallback_album=album_title)
            if t:
                tracks.append(t)

        display_name = f"{album_artist} - {album_title}" if album_artist else album_title
        return CollectionExport(
            kind="album",
            name=sanitize_filename(display_name, fallback="spotify_album"),
            artist=album_artist,
            album=album_title,
            total_tracks=max(total_tracks or 0, len(tracks)) or None,
            tracks=tracks,
            source_url=parsed.url,
            source_platform="spotify",
        )

    if parsed.kind == "spotify_track":
        data = _http_json(f"https://api.spotify.com/v1/tracks/{parsed.item_id}", headers=headers)
        t = _track_from_api_obj(data)
        if not t:
            raise SpotifyExtractionError("Impossibile leggere i metadati della traccia Spotify.")
        return CollectionExport(
            kind="single_track",
            name=sanitize_filename(f"{t.artist} - {t.title}", fallback="spotify_track"),
            artist=t.artist,
            album=t.album,
            total_tracks=1,
            tracks=[t],
            source_url=parsed.url,
            source_platform="spotify",
        )

    raise SpotifyExtractionError(f"Tipo Spotify non supportato: {parsed.kind}")


def _extract_via_embed(parsed: ParsedUrl) -> CollectionExport:
    stype = parsed.kind.replace("spotify_", "")
    embed_url = f"https://open.spotify.com/embed/{stype}/{parsed.item_id}"
    req = urllib.request.Request(embed_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        page_html = resp.read().decode("utf-8", errors="replace")

    match = _NEXT_DATA_RE.search(page_html)
    if not match:
        raise SpotifyExtractionError("Impossibile trovare __NEXT_DATA__ nella pagina embed Spotify.")

    payload = json.loads(match.group(1))
    entity = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )
    if not entity:
        raise SpotifyExtractionError("Dati Spotify embed vuoti.")

    name = _clean_text(entity.get("name") or entity.get("title") or "")
    subtitle = _clean_text(entity.get("subtitle") or "")

    if stype == "track":
        artist = subtitle.split(",")[0].strip()
        dur_ms = entity.get("duration")
        dur_sec = int(dur_ms // 1000) if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None
        if not artist or not name:
            raise SpotifyExtractionError("Metadati traccia embed incompleti.")
        track = Track(artist=artist, title=name, duration_sec=dur_sec)
        return CollectionExport(
            kind="single_track",
            name=sanitize_filename(f"{artist} - {name}"),
            artist=artist,
            total_tracks=1,
            tracks=[track],
            source_url=parsed.url,
            source_platform="spotify",
        )

    tracks: list[Track] = []
    album_name_for_tracks = name if stype == "album" else ""
    for item in entity.get("trackList") or []:
        title = _clean_text(item.get("title") or "")
        sub = _clean_text(item.get("subtitle") or "")
        artist = sub.split(",")[0].strip() if sub else subtitle.split(",")[0].strip()
        dur_ms = item.get("duration")
        dur_sec = int(dur_ms // 1000) if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None
        if title:
            tracks.append(Track(artist=artist, title=title, album=album_name_for_tracks, duration_sec=dur_sec))

    if stype == "album":
        album_artist = subtitle.split(",")[0].strip()
        display_name = f"{album_artist} - {name}" if album_artist else name
        return CollectionExport(
            kind="album",
            name=sanitize_filename(display_name, fallback="spotify_album"),
            artist=album_artist,
            album=name,
            total_tracks=len(tracks) or None,
            tracks=tracks,
            source_url=parsed.url,
            source_platform="spotify",
        )

    return CollectionExport(
        kind="playlist",
        name=sanitize_filename(name, fallback="spotify_playlist"),
        tracks=tracks,
        total_tracks=len(tracks),
        source_url=parsed.url,
        source_platform="spotify",
    )


def extract_spotify(parsed: ParsedUrl, client_id: str, client_secret: str) -> CollectionExport:
    """Extract Spotify playlist/album/track using API first, then Embed fallback."""
    api_error: Exception | None = None
    if client_id and client_secret:
        try:
            result = _extract_via_api(parsed, client_id, client_secret)
            if result.tracks:
                return result
        except (urllib.error.URLError, urllib.error.HTTPError, SpotifyExtractionError, KeyError, ValueError) as exc:
            api_error = exc

    try:
        result = _extract_via_embed(parsed)
        if not result.tracks:
            raise SpotifyExtractionError("Nessun brano trovato nell'embed Spotify.")
        return result
    except Exception as embed_exc:
        if api_error:
            raise SpotifyExtractionError(
                f"Estrazione Spotify fallita (API: {api_error}; Embed: {embed_exc})"
            ) from embed_exc
        raise SpotifyExtractionError(f"Estrazione Spotify fallita: {embed_exc}") from embed_exc
