"""Data models shared across extractors, LLM formatter, and downloader."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


CollectionKind = Literal["playlist", "album", "single_track"]
Platform = Literal["spotify", "youtube"]


@dataclass
class Track:
    """Normalized track metadata ready for CSV export, Soulseek search, and post-download tagging."""

    artist: str
    title: str
    album: str = ""
    duration_sec: int | None = None
    raw_title: str = ""
    uploader: str = ""
    album_artist: str = ""
    track_number: int | None = None
    total_tracks: int | None = None
    disc_number: int | None = None
    release_year: str = ""
    cover_url: str = ""


@dataclass
class CollectionExport:
    """Extracted playlist, full album, or single track collection."""

    kind: CollectionKind
    name: str
    tracks: list[Track] = field(default_factory=list)
    artist: str = ""
    album: str = ""
    total_tracks: int | None = None
    source_url: str = ""
    source_platform: Platform = "spotify"
    used_llm: bool = False
    cover_url: str = ""
