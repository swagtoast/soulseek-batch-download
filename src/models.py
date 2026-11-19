"""Data models shared across extractors, LLM formatter, and downloader."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


CollectionKind = Literal["playlist", "album", "single_track"]
Platform = Literal["spotify", "youtube"]


@dataclass
class Track:
    """Normalized track metadata ready for CSV export or Soulseek search."""

    artist: str
    title: str
    album: str = ""
    duration_sec: int | None = None
    raw_title: str = ""
    uploader: str = ""


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
