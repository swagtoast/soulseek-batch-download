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
            "convert_format": fmt,
            "convert_flac_to_aiff": fmt == "aiff",
            "delete_original": bool(delete_original),
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
    rc, username = _run_osascript(
        f'set r to display dialog "Benvenuto! Inserisci il tuo Username Soulseek (chiesto solo al primo avvio):" '
        f'with title "Soulseek Batch Download — Configurazione iniziale" '
        f'default answer "{esc_user}" buttons {{"Annulla", "Avanti"}} default button "Avanti"\n'
        f'return text returned of r'
    )
    if rc != 0:
        return {"confirmed": False}
    username = username.strip()
    if not username:
        return {"confirmed": False}

    rc, password = _run_osascript(
        'set r to display dialog "Inserisci la tua Password Soulseek:" '
        'with title "Soulseek Batch Download — Configurazione iniziale" '
        'default answer "" with hidden answer buttons {"Annulla", "Avanti"} default button "Avanti"\n'
        'return text returned of r'
    )
    if rc != 0 or not password:
        return {"confirmed": False}

    default_parent = cfg.output_dir if cfg.output_dir.is_dir() else (Path.home() / "Music")
    if not default_parent.is_dir():
        default_parent = Path.home()
    esc_dir = _applescript_escape(str(default_parent))
    rc, chosen_dir = _run_osascript(
        f'set f to choose folder with prompt "Scegli la cartella di download per Soulseek:" '
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
        f'with prompt "formato" default items {{"{default_item}"}} '
        f'OK button name "Avanti" cancel button name "Annulla"\n'
        'if c is false then error number -128\n'
        'return item 1 of c'
    )
    if rc != 0 or not chosen_fmt_label:
        return {"confirmed": False}
    chosen_fmt = fmt_map.get(chosen_fmt_label, "flac")

    delete_orig = cfg.delete_original
    if chosen_fmt in {"aiff", "mp3"}:
        def_btn = "Elimina file originale" if delete_orig else "Mantieni originale"
        rc, btn = _run_osascript(
            'set r to display dialog "Vuoi eliminare il file .flac originale dopo la conversione?" '
            'with title "Soulseek Batch Download — formato" '
            f'buttons {{"Annulla", "Mantieni originale", "Elimina file originale"}} '
            f'default button "{def_btn}"\n'
            'return button returned of r'
        )
        if rc != 0:
            return {"confirmed": False}
        delete_orig = btn == "Elimina file originale"

    return {
        "confirmed": True,
        "username": username,
        "password": password,
        "output_dir": chosen_dir,
        "convert_format": chosen_fmt,
        "delete_original": delete_orig,
    }


def _run_gui_setup(cfg: PipelineConfig) -> dict | None:
    existing_user, _ = _read_sockseek_creds(cfg.sockseek_conf)
    if existing_user in _PLACEHOLDER_VALUES:
        existing_user = ""

    if cfg.setup_bin.is_file() and os.access(cfg.setup_bin, os.X_OK):
        extra_args = [
            "--default-dir",
            str(cfg.output_dir),
            "--default-format",
            cfg.convert_format,
            "--default-delete",
            "true" if cfg.delete_original else "false",
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

    print("Formato:")
    print("  1) .flac (lossless)")
    print("  2) .aiff (PCM 24-bit lossless)")
    print("  3) .mp3 (320 kbps)")
    default_idx = {"flac": "1", "aiff": "2", "mp3": "3"}.get(cfg.convert_format, "1")
    ans = input(f"Scegli [1/2/3, default {default_idx}]: ").strip().lower().lstrip(".")
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
        dhint = "S/n" if del_orig else "s/N"
        dans = input(f"Elimina file originale dopo la conversione? [{dhint}]: ").strip().lower()
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
    print(f"[Setup] Configurazione salvata! Cartella download: {output_dir} | Formato: {fmt_label}")
    return load_config(cfg.config_file)
