#!/usr/bin/env python3
"""Lightweight Linux background watcher: highlight or copy any Spotify/YouTube link ->
shows a floating pill 'Scarica con Soulseek' near the cursor (X11 & Wayland, 0 pip deps).
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


SRC_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from i18n import t  # noqa: E402
from url_utils import classify_url, extract_url_from_text  # noqa: E402


def _read_selection_candidates() -> list[str]:
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

    found: list[str] = []
    for cmd in cmds:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=0.6, check=False)
            if proc.returncode == 0 and proc.stdout:
                s = proc.stdout.strip()
                if s and len(s) <= 2000:
                    found.append(s)
        except Exception:
            continue
    return found


def _extract_valid_music_url(raw: str) -> str:
    cand = extract_url_from_text(raw) or raw.strip()
    if not cand:
        return ""
    try:
        return classify_url(cand).url
    except Exception:
        return ""


def _launch_pipeline(url: str) -> None:
    launcher = PROJECT_ROOT / "Soulseek Batch Pipeline.sh"
    if not launcher.is_file():
        return
    subprocess.Popen(
        [str(launcher), url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> int:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return 0
    try:
        import tkinter as tk
    except Exception:
        return 0

    root = tk.Tk()
    root.withdraw()

    pill: dict = {"win": None, "after_id": None}
    seen_url = ""
    # Seed with current selection/clipboard so launching doesn't trigger on old text
    for raw in _read_selection_candidates():
        u = _extract_valid_music_url(raw)
        if u:
            seen_url = u
            break

    def hide_pill() -> None:
        if pill["after_id"] is not None:
            try:
                root.after_cancel(pill["after_id"])
            except Exception:
                pass
            pill["after_id"] = None
        if pill["win"] is not None:
            try:
                pill["win"].destroy()
            except Exception:
                pass
            pill["win"] = None

    def show_pill(url: str) -> None:
        hide_pill()
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#18181b")

        btn = tk.Button(
            win,
            text=t("ui_pill_download"),
            bg="#18181b",
            fg="#ffffff",
            activebackground="#2563eb",
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#3f3f46",
            font=("Sans", 10, "bold"),
            cursor="hand2",
            pady=6,
            padx=10,
            command=lambda: (hide_pill(), _launch_pipeline(url)),
        )
        btn.pack()

        try:
            px = max(12, root.winfo_pointerx() + 14)
            py = max(12, root.winfo_pointery() - 42)
        except Exception:
            px, py = 40, 40
        win.geometry(f"+{px}+{py}")

        pill["win"] = win
        pill["after_id"] = root.after(6000, hide_pill)

    def poll() -> None:
        nonlocal seen_url
        for raw in _read_selection_candidates():
            u = _extract_valid_music_url(raw)
            if u and u != seen_url:
                seen_url = u
                show_pill(u)
                break
        root.after(450, poll)

    root.after(500, poll)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
