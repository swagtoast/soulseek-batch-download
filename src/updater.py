"""Lightweight, safe, hardware-aware self-updater from GitHub (zero dependencies)."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

from config import PipelineConfig


REPO_SLUG = os.environ.get("SLSK_REPO_SLUG", "swagtoast/soulseek-batch-download")
REPO_BRANCH = os.environ.get("SLSK_REPO_BRANCH", "main")
CHECK_INTERVAL_SEC = 6 * 3600  # Check at most once every 6 hours unless --update is forced.


def _state_file(cfg: PipelineConfig) -> Path:
    return cfg.project_root / "config" / ".update_state.json"


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def fetch_remote_commit_sha(
    repo_slug: str = REPO_SLUG,
    branch: str = REPO_BRANCH,
    timeout: float = 2.5,
) -> str | None:
    """Fetch the latest 40-char commit SHA on GitHub (40-byte response, fast timeout)."""
    url = f"https://api.github.com/repos/{repo_slug}/commits/{branch}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.sha",
            "User-Agent": "soulseek-batch-download-updater/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            sha = resp.read().decode("utf-8", errors="ignore").strip()
            if len(sha) == 40 and all(c in "0123456789abcdefABCDEF" for c in sha):
                return sha.lower()
    except Exception:
        return None
    return None


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _safe_extract_tar(tar_bytes: bytes, dest_dir: Path) -> Path:
    dest_resolved = dest_dir.resolve()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        for member in tf.getmembers():
            member_path = (dest_resolved / member.name).resolve()
            if not str(member_path).startswith(str(dest_resolved) + os.sep) and member_path != dest_resolved:
                raise ValueError(f"Percorso archivio non sicuro: {member.name}")
            if member.issym() or member.islnk():
                continue
            tf.extract(member, path=dest_resolved)

    subdirs = [p for p in dest_resolved.iterdir() if p.is_dir()]
    if not subdirs:
        raise ValueError("Archivio aggiornamento vuoto.")
    return subdirs[0]


def _should_compile_swift(cfg: PipelineConfig) -> bool:
    if sys.platform != "darwin":
        return False
    hw = cfg.hardware
    if hw is not None and not (hw.is_apple_silicon or (hw.cpu_cores >= 6 and hw.ram_gb >= 16)):
        return False
    if not (shutil.which("swiftc") and shutil.which("xcode-select")):
        return False
    chk = subprocess.run(["xcode-select", "-p"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return chk.returncode == 0


def _recompile_swift_helpers_if_needed(cfg: PipelineConfig, old_hashes: dict[str, str]) -> None:
    if not _should_compile_swift(cfg):
        return
    for src_name, bin_name in (("slsk_prompt.swift", "slsk-prompt"), ("slsk_setup.swift", "slsk-setup")):
        src_path = cfg.project_root / "src" / src_name
        out_bin = cfg.project_root / "bin" / bin_name
        if not src_path.is_file():
            continue
        if out_bin.is_file() and _file_sha256(src_path) == old_hashes.get(src_name, ""):
            continue
        try:
            out_bin.parent.mkdir(parents=True, exist_ok=True)
            res = subprocess.run(
                ["swiftc", "-Onone", str(src_path), "-o", str(out_bin)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=25,
                check=False,
            )
            if res.returncode == 0 and out_bin.is_file():
                out_bin.chmod(0o755)
            else:
                out_bin.unlink(missing_ok=True)
        except subprocess.TimeoutExpired:
            out_bin.unlink(missing_ok=True)
            break


def apply_update_from_tarball(cfg: PipelineConfig, tar_bytes: bytes) -> None:
    """Safely apply code updates without touching credentials, downloads, models, or custom Finder icons."""
    root = cfg.project_root
    old_swift_hashes = {
        name: _file_sha256(root / "src" / name)
        for name in ("slsk_prompt.swift", "slsk_setup.swift")
    }

    with tempfile.TemporaryDirectory(prefix="slsk_update_") as tmp:
        extracted_root = _safe_extract_tar(tar_bytes, Path(tmp))
        new_src = extracted_root / "src"
        if not (new_src / "cli.py").is_file():
            raise ValueError("Archivio aggiornamento non valido (manca src/cli.py).")

        # Replace src/ cleanly via staging swap so an interrupted write never leaves half-copied files.
        staging_src = root / ".src_new"
        backup_src = root / ".src_old"
        if staging_src.exists():
            shutil.rmtree(staging_src, ignore_errors=True)
        if backup_src.exists():
            shutil.rmtree(backup_src, ignore_errors=True)
        shutil.copytree(new_src, staging_src)

        target_src = root / "src"
        try:
            if target_src.exists():
                target_src.rename(backup_src)
            staging_src.rename(target_src)
            if backup_src.exists():
                shutil.rmtree(backup_src, ignore_errors=True)
        except Exception:
            if backup_src.exists() and not target_src.exists():
                backup_src.rename(target_src)
            shutil.rmtree(staging_src, ignore_errors=True)
            raise

        # Update optional docs / tests / example configs (never touch real user configs or credentials).
        new_tests = extracted_root / "tests"
        if new_tests.is_dir():
            target_tests = root / "tests"
            if target_tests.exists():
                shutil.rmtree(target_tests, ignore_errors=True)
            shutil.copytree(new_tests, target_tests)

        for rel_name in ("README.md", "config/sockseek.conf.example", "config/pipeline.json.example"):
            src_f = extracted_root / rel_name
            if src_f.is_file():
                dst_f = root / rel_name
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                dst_f.write_bytes(src_f.read_bytes())

        # Update .command IN-PLACE to preserve any macOS Finder custom icon extended attribute!
        new_cmd = extracted_root / "Soulseek Batch Pipeline.command"
        if new_cmd.is_file():
            dst_cmd = root / "Soulseek Batch Pipeline.command"
            dst_cmd.write_bytes(new_cmd.read_bytes())
            dst_cmd.chmod(0o755)

        # Clean up any legacy JXA scripts.
        for legacy in ("slsk_setup.js", "slsk_prompt.js"):
            (target_src / legacy).unlink(missing_ok=True)

    _recompile_swift_helpers_if_needed(cfg, old_swift_hashes)


def check_and_apply_update(cfg: PipelineConfig, force: bool = False) -> bool:
    """Check GitHub for a newer commit and apply it safely. Returns True if code was updated."""
    if not force:
        if os.environ.get("SLSK_NO_UPDATE") == "1" or os.environ.get("SLSK_JUST_UPDATED") == "1":
            return False
        # Never auto-overwrite a local git development checkout unless --update was explicitly passed.
        if (cfg.project_root / ".git").exists():
            return False

    state_path = _state_file(cfg)
    state = _load_state(state_path)
    now = time.time()

    if not force and (now - float(state.get("last_check") or 0.0)) < CHECK_INTERVAL_SEC:
        return False

    remote_sha = fetch_remote_commit_sha(REPO_SLUG, REPO_BRANCH)
    state["last_check"] = now
    if not remote_sha:
        _save_state(state_path, state)
        if force:
            print("[Update] Impossibile contattare GitHub (offline o limite API).")
        return False

    local_sha = str(state.get("commit") or "").strip().lower()
    if local_sha == remote_sha:
        _save_state(state_path, state)
        if force:
            print(f"[Update] Già aggiornato all'ultima versione ({remote_sha[:7]}).")
        return False

    # If this is a git checkout and user forced --update, use git pull cleanly if possible, else warn.
    if (cfg.project_root / ".git").exists():
        print("[Update] Cartella git locale rilevata: aggiorno tramite git pull --ff-only...")
        proc = subprocess.run(
            ["git", "-C", str(cfg.project_root), "pull", "--ff-only"],
            check=False,
        )
        if proc.returncode == 0:
            state["commit"] = remote_sha
            _save_state(state_path, state)
            return True
        return False

    print(f"[Update] Nuova versione disponibile ({remote_sha[:7]}), aggiorno automaticamente...", flush=True)
    tar_url = f"https://github.com/{REPO_SLUG}/archive/refs/heads/{REPO_BRANCH}.tar.gz"
    try:
        req = urllib.request.Request(tar_url, headers={"User-Agent": "soulseek-batch-download-updater/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            tar_bytes = resp.read()
        apply_update_from_tarball(cfg, tar_bytes)
        state["commit"] = remote_sha
        state["updated_at"] = now
        _save_state(state_path, state)
        print(f"[Update] Aggiornamento completato ({remote_sha[:7]})! Riavvio con la nuova versione...\n", flush=True)
        return True
    except Exception as exc:
        _save_state(state_path, state)
        print(f"[Update] Aggiornamento saltato ({exc}); continuo con la versione attuale.")
        return False
