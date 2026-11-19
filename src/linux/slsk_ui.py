"""Native Linux GUI dialogs for Soulseek Batch Download (zero external pip dependencies).

3-tier automatic cascade tailored to the user's Linux desktop:
  1. Single-window tkinter UI (if python3-tk is available) — identical UX to macOS Swift / Windows WinForms.
  2. zenity (GNOME/GTK) or kdialog (KDE Plasma) — preinstalled on most Linux desktops.
  3. Returns None so caller cleanly falls back to interactive terminal prompts.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

from config import PipelineConfig, normalize_convert_format


_FORMAT_ITEMS = [
    (".flac (lossless)", "flac"),
    (".aiff (PCM 24-bit lossless)", "aiff"),
    (".mp3 (320 kbps)", "mp3"),
]
_MUSIC_HINTS = (
    "spotify.com/",
    "spotify.link/",
    "spoti.fi/",
    "spotify:",
    "youtube.com/",
    "youtu.be/",
)


def has_linux_display() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def has_linux_gui_backend() -> bool:
    if not has_linux_display():
        return False
    return _tk_available() or bool(shutil.which("zenity")) or bool(shutil.which("kdialog"))


def read_linux_clipboard_music_url() -> str:
    """Inspect Wayland/X11 clipboard and PRIMARY selection for a Spotify/YouTube URL."""
    cmds: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        cmds.append(["wl-paste", "--no-newline", "--primary"])
        cmds.append(["wl-paste", "--no-newline"])
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            cmds.append(["xclip", "-o", "-selection", "primary"])
            cmds.append(["xclip", "-o", "-selection", "clipboard"])
        elif shutil.which("xsel"):
            cmds.append(["xsel", "-o", "-p"])
            cmds.append(["xsel", "-o", "-b"])

    for cmd in cmds:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0, check=False)
            if proc.returncode == 0:
                val = (proc.stdout or "").strip()
                low = val.lower()
                if val and any(tok in low for tok in _MUSIC_HINTS):
                    return val
        except Exception:
            continue
    return ""


def _run_tk_setup(cfg: PipelineConfig, existing_user: str) -> dict | None:
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except Exception:
        return None

    result: dict = {"confirmed": False}
    try:
        root = tk.Tk()
    except Exception:
        return None

    root.title("Soulseek Batch Download — Configurazione iniziale")
    root.resizable(False, False)

    frm = ttk.Frame(root, padding=16)
    frm.grid(sticky="nsew")

    ttk.Label(
        frm,
        text="Configurazione iniziale (chiesta solo al primo avvio)",
        font=("Sans", 11, "bold"),
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

    ttk.Label(frm, text="Username Soulseek:").grid(row=1, column=0, sticky="w", pady=4)
    user_var = tk.StringVar(value=existing_user)
    user_entry = ttk.Entry(frm, textvariable=user_var, width=36)
    user_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

    ttk.Label(frm, text="Password Soulseek:").grid(row=2, column=0, sticky="w", pady=4)
    pass_var = tk.StringVar(value="")
    pass_entry = ttk.Entry(frm, textvariable=pass_var, width=36, show="•")
    pass_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

    ttk.Label(frm, text="Cartella download:").grid(row=3, column=0, sticky="w", pady=4)
    dir_var = tk.StringVar(value=str(cfg.output_dir))
    dir_entry = ttk.Entry(frm, textvariable=dir_var, width=28)
    dir_entry.grid(row=3, column=1, sticky="ew", padx=(8, 4), pady=4)

    def browse() -> None:
        init_dir = dir_var.get() if Path(dir_var.get()).is_dir() else str(Path.home())
        picked = filedialog.askdirectory(initialdir=init_dir, title="Scegli la cartella di download")
        if picked:
            dir_var.set(picked)

    ttk.Button(frm, text="Sfoglia…", command=browse).grid(row=3, column=2, sticky="e", pady=4)

    ttk.Label(frm, text="formato:").grid(row=4, column=0, sticky="w", pady=4)
    default_label = next((lbl for lbl, code in _FORMAT_ITEMS if code == cfg.convert_format), _FORMAT_ITEMS[0][0])
    fmt_var = tk.StringVar(value=default_label)
    fmt_combo = ttk.Combobox(
        frm,
        textvariable=fmt_var,
        values=[lbl for lbl, _ in _FORMAT_ITEMS],
        state="readonly",
        width=34,
    )
    fmt_combo.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

    err_var = tk.StringVar(value="")
    ttk.Label(frm, textvariable=err_var, foreground="#c62828").grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(4, 0)
    )

    btn_bar = ttk.Frame(frm)
    btn_bar.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))

    def on_cancel() -> None:
        result["confirmed"] = False
        root.destroy()

    def on_save() -> None:
        u = user_var.get().strip()
        p = pass_var.get()
        if not u or not p:
            err_var.set("Inserisci sia Username che Password Soulseek.")
            return
        label_to_code = {lbl: code for lbl, code in _FORMAT_ITEMS}
        result.update(
            {
                "confirmed": True,
                "username": u,
                "password": p,
                "output_dir": dir_var.get().strip() or str(cfg.output_dir),
                "convert_format": label_to_code.get(fmt_var.get(), "flac"),
                "delete_original": True,
            }
        )
        root.destroy()

    ttk.Button(btn_bar, text="Annulla", command=on_cancel).pack(side="left", padx=(0, 8))
    save_btn = ttk.Button(btn_bar, text="Salva e avvia", command=on_save)
    save_btn.pack(side="left")

    root.bind("<Return>", lambda _e: on_save())
    root.bind("<Escape>", lambda _e: on_cancel())
    (pass_entry if existing_user else user_entry).focus_set()
    root.mainloop()
    return result


def _run_tk_prompt(
    url: str | None,
    default_format: str,
    label: str | None = None,
) -> tuple[bool, str, str] | None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return None

    try:
        root = tk.Tk()
    except Exception:
        return None

    root.title("Soulseek Batch Download")
    root.resizable(False, False)
    state = {"confirmed": False, "url": (url or "").strip(), "fmt": normalize_convert_format(default_format)}

    frm = ttk.Frame(root, padding=16)
    frm.grid(sticky="nsew")

    row = 0
    if label:
        ttk.Label(frm, text=label, font=("Sans", 10, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        row += 1

    url_var = tk.StringVar(value=state["url"] or read_linux_clipboard_music_url())
    if not state["url"]:
        ttk.Label(frm, text="Link Spotify / YouTube:").grid(row=row, column=0, sticky="w", pady=4)
        url_entry = ttk.Entry(frm, textvariable=url_var, width=44)
        url_entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)
        row += 1
    else:
        url_entry = None

    ttk.Label(frm, text="formato:").grid(row=row, column=0, sticky="w", pady=4)
    default_label = next(
        (lbl for lbl, code in _FORMAT_ITEMS if code == state["fmt"]),
        _FORMAT_ITEMS[0][0],
    )
    fmt_var = tk.StringVar(value=default_label)
    fmt_combo = ttk.Combobox(
        frm,
        textvariable=fmt_var,
        values=[lbl for lbl, _ in _FORMAT_ITEMS],
        state="readonly",
        width=30,
    )
    fmt_combo.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)
    row += 1

    btn_bar = ttk.Frame(frm)
    btn_bar.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))

    def on_cancel() -> None:
        state["confirmed"] = False
        root.destroy()

    def on_ok() -> None:
        final_url = url_var.get().strip()
        if not final_url:
            return
        label_to_code = {lbl: code for lbl, code in _FORMAT_ITEMS}
        state["confirmed"] = True
        state["url"] = final_url
        state["fmt"] = label_to_code.get(fmt_var.get(), "flac")
        root.destroy()

    ttk.Button(btn_bar, text="Annulla", command=on_cancel).pack(side="left", padx=(0, 8))
    ttk.Button(btn_bar, text="Scarica", command=on_ok).pack(side="left")

    root.bind("<Return>", lambda _e: on_ok())
    root.bind("<Escape>", lambda _e: on_cancel())
    if url_entry is not None:
        url_entry.focus_set()
    else:
        fmt_combo.focus_set()
    root.mainloop()
    return (bool(state["confirmed"]), str(state["url"]), str(state["fmt"]))


def _run_zenity_setup(cfg: PipelineConfig, existing_user: str) -> dict | None:
    zenity = shutil.which("zenity")
    if not zenity:
        return None
    p1 = subprocess.run(
        [
            zenity,
            "--entry",
            "--title=Soulseek Batch Download — Configurazione iniziale",
            "--text=Inserisci il tuo Username Soulseek (chiesto solo al primo avvio):",
            f"--entry-text={existing_user}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p1.returncode != 0 or not p1.stdout.strip():
        return {"confirmed": False}
    username = p1.stdout.strip()

    p2 = subprocess.run(
        [
            zenity,
            "--password",
            "--title=Soulseek Batch Download — Password Soulseek",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p2.returncode != 0 or not p2.stdout.strip():
        return {"confirmed": False}
    password = p2.stdout.rstrip("\r\n")

    default_dir = str(cfg.output_dir if cfg.output_dir.is_dir() else Path.home()) + "/"
    p3 = subprocess.run(
        [
            zenity,
            "--file-selection",
            "--directory",
            "--title=Scegli la cartella di download per Soulseek",
            f"--filename={default_dir}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p3.returncode != 0 or not p3.stdout.strip():
        return {"confirmed": False}
    chosen_dir = p3.stdout.strip()

    args = [
        zenity,
        "--list",
        "--radiolist",
        "--title=Soulseek Batch Download",
        "--text=formato",
        "--column=Scelta",
        "--column=Formato",
    ]
    for lbl, code in _FORMAT_ITEMS:
        args.extend(["TRUE" if code == cfg.convert_format else "FALSE", lbl])
    p4 = subprocess.run(args, capture_output=True, text=True, check=False)
    if p4.returncode != 0 or not p4.stdout.strip():
        return {"confirmed": False}
    label_to_code = {lbl: code for lbl, code in _FORMAT_ITEMS}
    return {
        "confirmed": True,
        "username": username,
        "password": password,
        "output_dir": chosen_dir,
        "convert_format": label_to_code.get(p4.stdout.strip(), "flac"),
        "delete_original": True,
    }


def _run_zenity_prompt(
    url: str | None,
    default_format: str,
    label: str | None = None,
) -> tuple[bool, str, str] | None:
    zenity = shutil.which("zenity")
    if not zenity:
        return None
    final_url = (url or "").strip()
    if not final_url:
        clip = read_linux_clipboard_music_url()
        p_url = subprocess.run(
            [
                zenity,
                "--entry",
                "--title=Soulseek Batch Download",
                "--text=Incolla un URL Spotify o YouTube (playlist, album o video full album):",
                f"--entry-text={clip}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if p_url.returncode != 0 or not p_url.stdout.strip():
            return (False, "", default_format)
        final_url = p_url.stdout.strip()

    fmt_norm = normalize_convert_format(default_format)
    heading = f"{label} — formato" if label else "formato"
    args = [
        zenity,
        "--list",
        "--radiolist",
        "--title=Soulseek Batch Download",
        f"--text={heading}",
        "--ok-label=Scarica",
        "--column=Scelta",
        "--column=Formato",
    ]
    for lbl, code in _FORMAT_ITEMS:
        args.extend(["TRUE" if code == fmt_norm else "FALSE", lbl])
    p_fmt = subprocess.run(args, capture_output=True, text=True, check=False)
    if p_fmt.returncode != 0 or not p_fmt.stdout.strip():
        return (False, final_url, fmt_norm)
    label_to_code = {lbl: code for lbl, code in _FORMAT_ITEMS}
    return (True, final_url, label_to_code.get(p_fmt.stdout.strip(), "flac"))


def _run_kdialog_setup(cfg: PipelineConfig, existing_user: str) -> dict | None:
    kdialog = shutil.which("kdialog")
    if not kdialog:
        return None
    p1 = subprocess.run(
        [kdialog, "--title", "Soulseek Batch Download", "--inputbox", "Username Soulseek:", existing_user],
        capture_output=True,
        text=True,
        check=False,
    )
    if p1.returncode != 0 or not p1.stdout.strip():
        return {"confirmed": False}
    username = p1.stdout.strip()

    p2 = subprocess.run(
        [kdialog, "--title", "Soulseek Batch Download", "--password", "Password Soulseek:"],
        capture_output=True,
        text=True,
        check=False,
    )
    if p2.returncode != 0 or not p2.stdout.strip():
        return {"confirmed": False}
    password = p2.stdout.rstrip("\r\n")

    start_dir = str(cfg.output_dir if cfg.output_dir.is_dir() else Path.home())
    p3 = subprocess.run(
        [kdialog, "--title", "Scegli la cartella di download", "--getexistingdirectory", start_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    if p3.returncode != 0 or not p3.stdout.strip():
        return {"confirmed": False}
    chosen_dir = p3.stdout.strip()

    args = [kdialog, "--title", "Soulseek Batch Download", "--radiolist", "formato"]
    for lbl, code in _FORMAT_ITEMS:
        args.extend([code, lbl, "on" if code == cfg.convert_format else "off"])
    p4 = subprocess.run(args, capture_output=True, text=True, check=False)
    if p4.returncode != 0 or not p4.stdout.strip():
        return {"confirmed": False}
    return {
        "confirmed": True,
        "username": username,
        "password": password,
        "output_dir": chosen_dir,
        "convert_format": normalize_convert_format(p4.stdout.strip()),
        "delete_original": True,
    }


def _run_kdialog_prompt(
    url: str | None,
    default_format: str,
    label: str | None = None,
) -> tuple[bool, str, str] | None:
    kdialog = shutil.which("kdialog")
    if not kdialog:
        return None
    final_url = (url or "").strip()
    if not final_url:
        clip = read_linux_clipboard_music_url()
        p_url = subprocess.run(
            [
                kdialog,
                "--title",
                "Soulseek Batch Download",
                "--inputbox",
                "Incolla un URL Spotify o YouTube:",
                clip,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if p_url.returncode != 0 or not p_url.stdout.strip():
            return (False, "", default_format)
        final_url = p_url.stdout.strip()

    fmt_norm = normalize_convert_format(default_format)
    heading = f"{label} — formato" if label else "formato"
    args = [kdialog, "--title", "Soulseek Batch Download", "--radiolist", heading]
    for lbl, code in _FORMAT_ITEMS:
        args.extend([code, lbl, "on" if code == fmt_norm else "off"])
    p_fmt = subprocess.run(args, capture_output=True, text=True, check=False)
    if p_fmt.returncode != 0 or not p_fmt.stdout.strip():
        return (False, final_url, fmt_norm)
    return (True, final_url, normalize_convert_format(p_fmt.stdout.strip()))


def run_linux_setup(cfg: PipelineConfig, existing_user: str) -> dict | None:
    if not has_linux_display():
        return None
    if _tk_available():
        res = _run_tk_setup(cfg, existing_user)
        if res is not None:
            return res
    if shutil.which("zenity"):
        return _run_zenity_setup(cfg, existing_user)
    if shutil.which("kdialog"):
        return _run_kdialog_setup(cfg, existing_user)
    return None


def run_linux_prompt(
    url: str | None,
    default_format: str,
    label: str | None = None,
) -> tuple[bool, str, str] | None:
    if not has_linux_display():
        return None
    if _tk_available():
        res = _run_tk_prompt(url, default_format, label=label)
        if res is not None:
            return res
    if shutil.which("zenity"):
        return _run_zenity_prompt(url, default_format, label=label)
    if shutil.which("kdialog"):
        return _run_kdialog_prompt(url, default_format, label=label)
    return None
