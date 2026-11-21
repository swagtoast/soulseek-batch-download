"""First-run-only Soulseek credentials & Finder folder-picker setup."""

from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from config import PipelineConfig, load_config, normalize_convert_format
from i18n import is_italian, t


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
    convert_format: str | bool = "flac",
    delete_original: bool = True,
) -> None:
    """Persist Soulseek credentials and pipeline preferences with owner-only (600) permissions."""
    fmt = normalize_convert_format(convert_format)
    cfg.sockseek_conf.parent.mkdir(parents=True, exist_ok=True)
    cfg.config_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_out = output_dir.expanduser()
    resolved_out.mkdir(parents=True, exist_ok=True)

    pref_fmt = "mp3" if fmt == "mp3" else ("aiff,flac,wav" if fmt == "aiff" else "flac,aiff,wav")
    format_list = "mp3,flac,aiff,wav" if fmt == "mp3" else "flac,aiff,wav,mp3"
    cfg.sockseek_conf.write_text(
        f"username = {username}\n"
        f"password = {password}\n"
        f"output-dir = {resolved_out}\n"
        f"pref-format = {pref_fmt}\n"
        f"format = {format_list}\n"
        "pref-min-bitrate = 320\n"
        "length-tol = 5\n"
        "pref-length-tol = 5\n"
        "fast-search = true\n"
        "write-index = false\n",
        encoding="utf-8",
    )
    if os.name != "nt":
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
            "convert_format": fmt,
            "convert_flac_to_aiff": fmt == "aiff",
            "delete_original": bool(delete_original),
            "setup_completed": True,
        }
    )
    cfg.config_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(cfg.config_file, 0o600)


def _can_use_gui(no_gui: bool) -> bool:
    if no_gui:
        return False
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if sys.platform == "darwin" or os.name == "nt":
        return True
    if sys.platform.startswith("linux"):
        try:
            from linux.slsk_ui import has_linux_gui_backend
            return has_linux_gui_backend()
        except Exception:
            return False
    return False


def _applescript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _run_osascript(script: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "").strip()


def _run_applescript_setup(cfg: PipelineConfig, existing_user: str) -> dict | None:
    """100% native macOS StandardAdditions dialogs (works on all Intel/Apple Silicon macOS, 0% CPU)."""
    if not Path("/usr/bin/osascript").is_file():
        return None

    esc_user = _applescript_escape(existing_user)
    esc_title = _applescript_escape(t("setup_title"))
    esc_cancel = _applescript_escape(t("ui_btn_cancel"))
    esc_next = _applescript_escape(t("ui_btn_next"))
    esc_save = _applescript_escape(t("ui_btn_save"))
    rc, username = _run_osascript(
        f'set r to display dialog "{_applescript_escape(t("setup_user_dialog"))}" '
        f'with title "{esc_title}" '
        f'default answer "{esc_user}" buttons {{"{esc_cancel}", "{esc_next}"}} default button "{esc_next}"\n'
        f'return text returned of r'
    )
    if rc != 0:
        return {"confirmed": False}
    username = username.strip()
    if not username:
        return {"confirmed": False}

    rc, password = _run_osascript(
        f'set r to display dialog "{_applescript_escape(t("setup_pass_dialog"))}" '
        f'with title "{esc_title}" '
        f'default answer "" with hidden answer buttons {{"{esc_cancel}", "{esc_next}"}} default button "{esc_next}"\n'
        f'return text returned of r'
    )
    if rc != 0 or not password:
        return {"confirmed": False}

    default_parent = cfg.output_dir if cfg.output_dir.is_dir() else (Path.home() / "Music")
    if not default_parent.is_dir():
        default_parent = Path.home()
    esc_dir = _applescript_escape(str(default_parent))
    rc, chosen_dir = _run_osascript(
        f'set f to choose folder with prompt "{_applescript_escape(t("setup_dir_dialog"))}" '
        f'default location (POSIX file "{esc_dir}")\n'
        f'return POSIX path of f'
    )
    if rc != 0 or not chosen_dir:
        return {"confirmed": False}
    chosen_dir = chosen_dir.rstrip("/") or str(cfg.output_dir)

    items = [
        ".flac (lossless)",
        ".aiff (PCM 24-bit lossless)",
        ".mp3 (320 kbps)",
    ]
    fmt_map = {
        items[0]: "flac",
        items[1]: "aiff",
        items[2]: "mp3",
    }
    default_item = {"flac": items[0], "aiff": items[1], "mp3": items[2]}.get(cfg.convert_format, items[0])
    rc, chosen_fmt_label = _run_osascript(
        'set opts to {".flac (lossless)", ".aiff (PCM 24-bit lossless)", ".mp3 (320 kbps)"}\n'
        f'set c to choose from list opts with title "Soulseek Batch Download" '
        f'with prompt "{_applescript_escape(t("ui_format_label"))}" default items {{"{default_item}"}} '
        f'OK button name "{esc_save}" cancel button name "{esc_cancel}"\n'
        'if c is false then error number -128\n'
        'return item 1 of c'
    )
    if rc != 0 or not chosen_fmt_label:
        return {"confirmed": False}
    chosen_fmt = fmt_map.get(chosen_fmt_label, "flac")

    return {
        "confirmed": True,
        "username": username,
        "password": password,
        "output_dir": chosen_dir,
        "convert_format": chosen_fmt,
        "delete_original": True,
    }


def _run_windows_setup(cfg: PipelineConfig, existing_user: str) -> dict | None:
    if not cfg.win_setup_ps1.is_file():
        return None
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-STA",
        "-File",
        str(cfg.win_setup_ps1),
        "-DefaultDir",
        str(cfg.output_dir),
        "-DefaultFormat",
        cfg.convert_format,
    ]
    if existing_user:
        cmd.extend(["-DefaultUser", existing_user])
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"confirmed": False}
    try:
        return json.loads(raw.splitlines()[-1])
    except json.JSONDecodeError:
        return None


def _run_gui_setup(cfg: PipelineConfig) -> dict | None:
    existing_user, _ = _read_sockseek_creds(cfg.sockseek_conf)
    if existing_user in _PLACEHOLDER_VALUES:
        existing_user = ""

    if os.name == "nt":
        return _run_windows_setup(cfg, existing_user)

    if sys.platform.startswith("linux"):
        try:
            from linux.slsk_ui import run_linux_setup
            return run_linux_setup(cfg, existing_user)
        except Exception:
            return None

    if cfg.setup_bin.is_file() and os.access(cfg.setup_bin, os.X_OK):
        extra_args = [
            "--default-dir",
            str(cfg.output_dir),
            "--default-format",
            cfg.convert_format,
        ]
        if existing_user:
            extra_args.extend(["--default-user", existing_user])
        proc = subprocess.run([str(cfg.setup_bin), *extra_args], capture_output=True, text=True, check=False)
        raw = (proc.stdout or "").strip()
        if not raw:
            return {"confirmed": False}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    return _run_applescript_setup(cfg, existing_user)


def _run_terminal_setup(cfg: PipelineConfig) -> dict:
    print(t("setup_term_header"))
    print(t("setup_term_sub"))
    existing_user, _ = _read_sockseek_creds(cfg.sockseek_conf)
    if existing_user in _PLACEHOLDER_VALUES:
        existing_user = ""

    user_lbl = t("setup_user_label").rstrip(":")
    while True:
        prompt_u = f"{user_lbl} [{existing_user}]: " if existing_user else f"{user_lbl}: "
        u = input(prompt_u).strip() or existing_user
        if u:
            break
    while True:
        p = getpass.getpass(t("setup_pass_term"))
        if p:
            break

    default_dir = str(cfg.output_dir)
    dir_lbl = t("setup_dir_label").rstrip(":")
    d = input(f"{dir_lbl} [{default_dir}]: ").strip() or default_dir
    d = str(Path(d).expanduser())

    print(t("setup_format_heading"))
    print("  1) .flac (lossless)")
    print("  2) .aiff (PCM 24-bit lossless)")
    print("  3) .mp3 (320 kbps)")
    default_idx = {"flac": "1", "aiff": "2", "mp3": "3"}.get(cfg.convert_format, "1")
    ans = input(t("setup_choose_123", default=default_idx)).strip().lower().lstrip(".")
    if ans in {"2", "aiff", "aif"}:
        conv_fmt = "aiff"
    elif ans in {"3", "mp3", "320"}:
        conv_fmt = "mp3"
    elif ans in {"1", "flac", "none", "no", "n"}:
        conv_fmt = "flac"
    else:
        conv_fmt = cfg.convert_format

    del_orig = cfg.delete_original
    if conv_fmt in {"aiff", "mp3"}:
        dhint = ("S/n" if del_orig else "s/N") if is_italian() else ("Y/n" if del_orig else "y/N")
        dans = input(t("setup_delete_orig_q", hint=dhint)).strip().lower()
        if dans in {"s", "si", "sì", "y", "yes"}:
            del_orig = True
        elif dans in {"n", "no"}:
            del_orig = False

    return {
        "confirmed": True,
        "username": u,
        "password": p,
        "output_dir": d,
        "convert_format": conv_fmt,
        "delete_original": del_orig,
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
    raw_fmt = result.get("convert_format", result.get("convert_flac_to_aiff", "flac"))
    convert_fmt = normalize_convert_format(raw_fmt)
    delete_original = bool(result.get("delete_original", True))

    if not username or not password:
        return None

    write_setup_configs(cfg, username, password, output_dir, convert_fmt, delete_original)
    fmt_label = {"flac": ".flac", "aiff": ".aiff (24-bit)", "mp3": ".mp3 (320 kbps)"}[convert_fmt]
    print(t("setup_saved", dir=output_dir, fmt=fmt_label))
    return load_config(cfg.config_file)
