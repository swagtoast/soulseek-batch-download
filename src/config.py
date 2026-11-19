"""Configuration loader for paths, Spotify credentials, Ollama, ffmpeg, and Sockseek."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class PipelineConfig:
    """Runtime configuration resolved from config/pipeline.json and environment."""

    project_root: Path
    config_file: Path
    output_dir: Path
    lists_dir: Path
    sockseek_bin: Path
    sockseek_conf: Path
    prompt_bin: Path
    prompt_js: Path
    yt_dlp_bin: str
    deno_bin: str
    ffmpeg_bin: str
    ffprobe_bin: str
    ollama_bin: str
    ollama_url: str
    ollama_model: str
    llm_batch_size: int
    spotify_client_id: str
    spotify_client_secret: str
    convert_flac_to_aiff: bool


def _resolve_binary(configured: str, fallback_name: str, extra_paths: tuple[str, ...]) -> str:
    local_bin = PROJECT_ROOT / "bin" / fallback_name
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)
    if configured:
        expanded = Path(configured).expanduser()
        if expanded.is_file() and os.access(expanded, os.X_OK):
            return str(expanded)
    found = shutil.which(fallback_name)
    if found:
        return found
    for candidate in extra_paths:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return configured or fallback_name


def load_config(config_path: Path | None = None) -> PipelineConfig:
    """Load pipeline configuration from JSON with environment overrides."""
    cfg_file = config_path or (PROJECT_ROOT / "config" / "pipeline.json")
    raw: dict = {}
    if cfg_file.is_file():
        raw = json.loads(cfg_file.read_text(encoding="utf-8"))

    default_downloads = str(Path.home() / "Music" / "Soulseek Downloads")
    output_dir = Path(
        os.environ.get("SLSK_OUTPUT_DIR")
        or raw.get("output_dir")
        or default_downloads
    ).expanduser()

    lists_dir = Path(raw.get("lists_dir") or (PROJECT_ROOT / "lists")).expanduser()
    sockseek_bin = Path(raw.get("sockseek_path") or (PROJECT_ROOT / "bin" / "sockseek")).expanduser()
    sockseek_conf = Path(raw.get("sockseek_conf") or (PROJECT_ROOT / "config" / "sockseek.conf")).expanduser()
    prompt_bin = Path(raw.get("prompt_path") or (PROJECT_ROOT / "bin" / "slsk-prompt")).expanduser()
    prompt_js = PROJECT_ROOT / "src" / "slsk_prompt.js"

    yt_dlp_bin = _resolve_binary(
        str(raw.get("yt_dlp_path", "")),
        "yt-dlp",
        ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"),
    )
    deno_bin = _resolve_binary(
        str(raw.get("deno_path", "")),
        "deno",
        ("/opt/homebrew/bin/deno", "/usr/local/bin/deno"),
    )
    ffmpeg_bin = _resolve_binary(
        str(raw.get("ffmpeg_path", "")),
        "ffmpeg",
        ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"),
    )
    ffprobe_bin = _resolve_binary(
        str(raw.get("ffprobe_path", "")),
        "ffprobe",
        ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"),
    )
    ollama_bin = _resolve_binary(
        str(raw.get("ollama_path", "")),
        "ollama",
        ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama"),
    )

    return PipelineConfig(
        project_root=PROJECT_ROOT,
        config_file=cfg_file,
        output_dir=output_dir,
        lists_dir=lists_dir,
        sockseek_bin=sockseek_bin,
        sockseek_conf=sockseek_conf,
        prompt_bin=prompt_bin,
        prompt_js=prompt_js,
        yt_dlp_bin=yt_dlp_bin,
        deno_bin=deno_bin,
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        ollama_bin=ollama_bin,
        ollama_url=str(os.environ.get("OLLAMA_HOST") or raw.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=str(os.environ.get("OLLAMA_MODEL") or raw.get("ollama_model") or "auto"),
        llm_batch_size=max(5, int(raw.get("llm_batch_size") or 25)),
        spotify_client_id=str(os.environ.get("SPOTIFY_CLIENT_ID") or raw.get("spotify_client_id") or ""),
        spotify_client_secret=str(os.environ.get("SPOTIFY_CLIENT_SECRET") or raw.get("spotify_client_secret") or ""),
        convert_flac_to_aiff=bool(raw.get("convert_flac_to_aiff", False)),
    )


def save_convert_preference(cfg: PipelineConfig, enabled: bool) -> None:
    """Persist the FLAC->AIFF checkbox preference cleanly in config/pipeline.json."""
    if not cfg.config_file.is_file():
        return
    try:
        raw = json.loads(cfg.config_file.read_text(encoding="utf-8"))
        if raw.get("convert_flac_to_aiff") == enabled:
            return
        raw["convert_flac_to_aiff"] = bool(enabled)
        cfg.config_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
