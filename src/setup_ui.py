"""First-run-only Soulseek credentials & Finder folder-picker setup."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from config import PipelineConfig, load_config


_PLACEHOLDER_VALUES = {
    "",
    "YOUR_SOULSEEK_USERNAME",
    "YOUR_SOULSEEK_PASSWORD",
    "your-soulseek-username",
    "your-soulseek-password",
}


def _read_sockseek_creds(conf_path: Path) -> tuple[str, str]:
    if not conf_path.is_file():
        return "", ""
    username = ""
    password = ""
    try:
        for line in conf_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, val = stripped.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if key == "username":
                username = val
            elif key == "password":
                password = val
    except OSError:
        return "", ""
    return username, password


def is_first_run_needed(cfg: PipelineConfig) -> bool:
    """Return True if Soulseek credentials have not been configured yet."""
    user, pwd = _read_sockseek_creds(cfg.sockseek_conf)
    return user in _PLACEHOLDER_VALUES or pwd in _PLACEHOLDER_VALUES


def write_setup_configs(
    cfg: PipelineConfig,
    username: str,
    password: str,
    output_dir: Path,
    convert_flac_to_aiff: bool,
) -> None:
    """Persist Soulseek credentials and pipeline preferences with owner-only (600) permissions."""
    cfg.sockseek_conf.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_out = output_dir.expanduser()
    resolved_out.mkdir(parents=True, exist_ok=True)

    cfg.sockseek_conf.write_text(
        f"username = {username}\n"
        f"password = {password}\n"
        f"output-dir = {resolved_out}\n"
        "pref-format = flac,aiff,wav\n"
        "format = flac,aiff,wav,mp3\n"
        "pref-min-bitrate = 320\n"
        "length-tol = 5\n"
        "pref-length-tol = 5\n"
        "fast-search = true\n"
        "write-index = true\n",
        encoding="utf-8",
    )
    os.chmod(cfg.sockseek_conf, 0o600)

    existing: dict = {}
    if cfg.config_file.is_file():
        try:
            existing = json.loads(cfg.config_file.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing.update(
        {
            "output_dir": str(resolved_out),
            "spotify_client_id": existing.get("spotify_client_id", ""),
            "spotify_client_secret": existing.get("spotify_client_secret", ""),
            "ollama_url": existing.get("ollama_url", "http://127.0.0.1:11434"),
            "ollama_model": existing.get("ollama_model", "auto"),
            "llm_batch_size": existing.get("llm_batch_size", 25),
            "convert_flac_to_aiff": bool(convert_flac_to_aiff),
            "setup_completed": True,
        }
    )
    cfg.config_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    os.chmod(cfg.config_file, 0o600)


def _can_use_gui(no_gui: bool) -> bool:
    if no_gui or sys.platform != "darwin":
        return False
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    return True


def _run_gui_setup(cfg: PipelineConfig) -> dict | None:
    existing_user, _ = _read_sockseek_creds(cfg.sockseek_conf)
    if existing_user in _PLACEHOLDER_VALUES:
        existing_user = ""

    extra_args = [
        "--default-dir",
        str(cfg.output_dir),
        "--default-convert",
        "true" if cfg.convert_flac_to_aiff else "false",
    ]
    if existing_user:
        extra_args.extend(["--default-user", existing_user])

    if cfg.setup_bin.is_file() and os.access(cfg.setup_bin, os.X_OK):
        cmd = [str(cfg.setup_bin), *extra_args]
    elif cfg.setup_js.is_file() and Path("/usr/bin/osascript").is_file():
        cmd = ["/usr/bin/osascript", "-l", "JavaScript", str(cfg.setup_js), *extra_args]
    else:
        return None

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"confirmed": False}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _run_terminal_setup(cfg: PipelineConfig) -> dict:
    print("\n=== Configurazione iniziale — Soulseek Batch Download ===")
    print("Queste impostazioni vengono chieste solo al primo avvio.\n")
    existing_user, _ = _read_sockseek_creds(cfg.sockseek_conf)
    if existing_user in _PLACEHOLDER_VALUES:
        existing_user = ""

    while True:
        prompt_u = f"Username Soulseek [{existing_user}]: " if existing_user else "Username Soulseek: "
        u = input(prompt_u).strip() or existing_user
        if u:
            break
    while True:
        p = getpass.getpass("Password Soulseek (nascosta): ")
        if p:
            break

    default_dir = str(cfg.output_dir)
    d = input(f"Cartella download [{default_dir}]: ").strip() or default_dir
    d = str(Path(d).expanduser())

    hint = "S/n" if cfg.convert_flac_to_aiff else "s/N"
    ans = input(f"Convertire automaticamente FLAC -> AIFF (MP3 invariati)? [{hint}]: ").strip().lower()
    if ans in {"s", "si", "sì", "y", "yes"}:
        conv = True
    elif ans in {"n", "no"}:
        conv = False
    else:
        conv = cfg.convert_flac_to_aiff

    return {
        "confirmed": True,
        "username": u,
        "password": p,
        "output_dir": d,
        "convert_flac_to_aiff": conv,
    }


def ensure_configured(cfg: PipelineConfig, force_setup: bool = False, no_gui: bool = False) -> PipelineConfig | None:
    """Run first-run-only setup if unconfigured (or if --setup passed). Returns updated config or None if cancelled."""
    if not force_setup and not is_first_run_needed(cfg):
        return cfg

    result = _run_gui_setup(cfg) if _can_use_gui(no_gui) else None
    if result is None:
        try:
            result = _run_terminal_setup(cfg)
        except (EOFError, KeyboardInterrupt):
            return None

    if not result.get("confirmed"):
        return None

    username = re.sub(r"[\r\n]+", "", str(result.get("username") or "")).strip()
    password = re.sub(r"[\r\n]+", "", str(result.get("password") or ""))
    output_dir = Path(str(result.get("output_dir") or cfg.output_dir)).expanduser()
    convert_aiff = bool(result.get("convert_flac_to_aiff", False))

    if not username or not password:
        return None

    write_setup_configs(cfg, username, password, output_dir, convert_aiff)
    print(f"[Setup] Configurazione salvata! Cartella download: {output_dir}")
    return load_config(cfg.config_file)
