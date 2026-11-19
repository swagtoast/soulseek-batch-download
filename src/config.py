"""Configuration loader for paths, Spotify credentials, Ollama, ffmpeg, and Sockseek."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VALID_CONVERT_FORMATS = ("flac", "aiff", "mp3")


@dataclass
class HardwareProfile:
    """Detected Mac hardware profile used to auto-tune parallelism, UI, and LLM memory limits."""

    arch: str
    cpu_cores: int
    ram_gb: int
    is_apple_silicon: bool
    recommended_llm_batch_size: int
    recommended_yt_workers: int
    max_ollama_model_billions: int


def detect_hardware_profile() -> HardwareProfile:
    """Inspect local CPU architecture, core count, and RAM (pure stdlib, <5ms)."""
    arch = platform.machine().lower() or "unknown"
    cpu_cores = max(1, os.cpu_count() or 2)
    ram_gb = 8

    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                ram_gb = max(1, int(round(int(proc.stdout.strip()) / (1024 ** 3))))
        except Exception:
            pass
    elif os.name == "nt":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                ram_gb = max(1, int(round(stat.ullTotalPhys / (1024 ** 3))))
        except Exception:
            pass
    elif hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0:
                ram_gb = max(1, int(round((pages * page_size) / (1024 ** 3))))
        except Exception:
            pass

    is_apple_silicon = sys.platform == "darwin" and arch in {"arm64", "aarch64"}
    low_resource = ram_gb <= 8 or cpu_cores <= 4
    recommended_llm_batch_size = 15 if low_resource else 25
    recommended_yt_workers = 2 if low_resource else 4
    if ram_gb <= 8:
        max_ollama_b = 3
    elif ram_gb < 16:
        max_ollama_b = 8
    else:
        max_ollama_b = 16

    return HardwareProfile(
        arch=arch,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        is_apple_silicon=is_apple_silicon,
        recommended_llm_batch_size=recommended_llm_batch_size,
        recommended_yt_workers=recommended_yt_workers,
        max_ollama_model_billions=max_ollama_b,
    )


def normalize_convert_format(value: object) -> str:
    """Normalize a target format setting into 'flac' (no conversion), 'aiff', or 'mp3'."""
    if isinstance(value, bool):
        return "aiff" if value else "flac"
    s = str(value or "").strip().lower().lstrip(".")
    if s in {"aiff", "aif", "true", "1", "yes"}:
        return "aiff"
    if s in {"mp3", "mp3_320", "mp3-320", "320"}:
        return "mp3"
    return "flac"


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
    setup_bin: Path
    win_prompt_ps1: Path
    win_setup_ps1: Path
    mlx_python: str
    mlx_base_model: str
    mlx_adapter_dir: Path
    mlx_tagger_adapter_dir: Path
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
    convert_format: str
    delete_original: bool = True
    hardware: HardwareProfile | None = None

    def __post_init__(self) -> None:
        if self.hardware is None:
            self.hardware = detect_hardware_profile()

    @property
    def convert_flac_to_aiff(self) -> bool:
        return self.convert_format == "aiff"


def _resolve_binary(configured: str, fallback_name: str, extra_paths: tuple[str, ...]) -> str:
    exe_names = (f"{fallback_name}.exe", fallback_name) if os.name == "nt" else (fallback_name,)
    for name in exe_names:
        local_bin = PROJECT_ROOT / "bin" / name
        if local_bin.is_file() and (os.name == "nt" or os.access(local_bin, os.X_OK)):
            return str(local_bin)
    if configured:
        expanded = Path(configured).expanduser()
        if expanded.is_file() and (os.name == "nt" or os.access(expanded, os.X_OK)):
            return str(expanded)
    for name in exe_names:
        found = shutil.which(name)
        if found:
            return found
    for candidate in extra_paths:
        if Path(candidate).is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return candidate
    return configured or exe_names[0]


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

    hardware = detect_hardware_profile()

    lists_dir = Path(raw.get("lists_dir") or (PROJECT_ROOT / "lists")).expanduser()
    sockseek_default = PROJECT_ROOT / "bin" / ("sockseek.exe" if os.name == "nt" else "sockseek")
    sockseek_bin = Path(raw.get("sockseek_path") or sockseek_default).expanduser()
    sockseek_conf = Path(raw.get("sockseek_conf") or (PROJECT_ROOT / "config" / "sockseek.conf")).expanduser()
    prompt_bin = Path(raw.get("prompt_path") or (PROJECT_ROOT / "bin" / "slsk-prompt")).expanduser()
    setup_bin = Path(raw.get("setup_path") or (PROJECT_ROOT / "bin" / "slsk-setup")).expanduser()
    win_prompt_ps1 = PROJECT_ROOT / "src" / "win" / "slsk_prompt.ps1"
    win_setup_ps1 = PROJECT_ROOT / "src" / "win" / "slsk_setup.ps1"

    default_mlx_py = Path.home() / "llm_wiki_agent" / ".venv" / "bin" / "python"
    mlx_python = str(
        Path(
            os.environ.get("SLSK_MLX_PYTHON")
            or raw.get("mlx_python")
            or (str(default_mlx_py) if default_mlx_py.is_file() else "")
        ).expanduser()
    ) if (os.environ.get("SLSK_MLX_PYTHON") or raw.get("mlx_python") or default_mlx_py.is_file()) else ""

    default_mlx_model = Path.home() / "llm_wiki_agent" / "models" / "MiniCPM5-2B-MLX"
    mlx_base_model = str(
        Path(
            os.environ.get("SLSK_MLX_MODEL")
            or raw.get("mlx_base_model")
            or (str(default_mlx_model) if default_mlx_model.is_dir() else "")
        ).expanduser()
    ) if (os.environ.get("SLSK_MLX_MODEL") or raw.get("mlx_base_model") or default_mlx_model.is_dir()) else ""

    mlx_adapter_dir = Path(
        os.environ.get("SLSK_MLX_ADAPTER")
        or raw.get("mlx_adapter_dir")
        or (PROJECT_ROOT / "models" / "minicpm5_slsk_lora")
    ).expanduser()
    mlx_tagger_adapter_dir = Path(
        os.environ.get("SLSK_MLX_TAGGER_ADAPTER")
        or raw.get("mlx_tagger_adapter_dir")
        or (PROJECT_ROOT / "models" / "minicpm5_tagger_lora")
    ).expanduser()

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
        setup_bin=setup_bin,
        win_prompt_ps1=win_prompt_ps1,
        win_setup_ps1=win_setup_ps1,
        mlx_python=mlx_python,
        mlx_base_model=mlx_base_model,
        mlx_adapter_dir=mlx_adapter_dir,
        mlx_tagger_adapter_dir=mlx_tagger_adapter_dir,
        yt_dlp_bin=yt_dlp_bin,
        deno_bin=deno_bin,
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        ollama_bin=ollama_bin,
        ollama_url=str(os.environ.get("OLLAMA_HOST") or raw.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=str(os.environ.get("OLLAMA_MODEL") or raw.get("ollama_model") or "auto"),
        llm_batch_size=max(5, int(raw.get("llm_batch_size") or hardware.recommended_llm_batch_size)),
        spotify_client_id=str(os.environ.get("SPOTIFY_CLIENT_ID") or raw.get("spotify_client_id") or ""),
        spotify_client_secret=str(os.environ.get("SPOTIFY_CLIENT_SECRET") or raw.get("spotify_client_secret") or ""),
        convert_format=(
            normalize_convert_format(raw["convert_format"])
            if "convert_format" in raw
            else normalize_convert_format(raw.get("convert_flac_to_aiff", False))
        ),
        delete_original=bool(raw.get("delete_original", True)),
        hardware=hardware,
    )


def save_convert_preference(
    cfg: PipelineConfig,
    target_format: str | bool,
    delete_original: bool | None = None,
) -> None:
    """Persist target format ('flac', 'aiff', or 'mp3') and delete_original flag in config/pipeline.json."""
    if not cfg.config_file.is_file():
        return
    fmt = normalize_convert_format(target_format)
    try:
        raw = json.loads(cfg.config_file.read_text(encoding="utf-8"))
        del_orig = bool(raw.get("delete_original", True)) if delete_original is None else bool(delete_original)
        if (
            raw.get("convert_format") == fmt
            and raw.get("convert_flac_to_aiff") == (fmt == "aiff")
            and raw.get("delete_original") == del_orig
        ):
            return
        raw["convert_format"] = fmt
        raw["convert_flac_to_aiff"] = fmt == "aiff"
        raw["delete_original"] = del_orig
        cfg.config_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
