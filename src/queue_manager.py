"""Cross-platform crash-safe download queue & overnight sleep inhibitor (macOS, Linux, Windows).

Ensures only ONE Soulseek session runs at a time across all terminal windows, Services,
floating pills, and browser-extension triggers. Any download launched while another is
running is atomically enqueued into `queue/pending/` and picked up automatically in FIFO
order by the active worker as soon as the current download finishes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]


@dataclass
class QueueJob:
    """Single queued download request with its own per-item format and options."""

    url: str
    mode: str = "auto"
    convert_format: str = "flac"
    delete_original: bool = True
    open_finder: bool = True
    auto_tag: bool = True
    job_id: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.job_id:
            self.job_id = uuid.uuid4().hex[:8]
        if not self.created_at:
            self.created_at = time.time()


def get_queue_dir(project_root: Path) -> Path:
    return project_root / "queue"


def _pending_dir(queue_dir: Path) -> Path:
    return queue_dir / "pending"


class QueueLock:
    """OS-enforced non-blocking exclusive lock (auto-released by kernel on crash/exit)."""

    def __init__(self, queue_dir: Path):
        self.queue_dir = queue_dir
        self.lock_path = queue_dir / "active.lock"
        self._fp = None
        self.acquired = False

    def try_acquire(self) -> bool:
        if self.acquired:
            return True
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        fp = self.lock_path.open("a+b")
        try:
            if fcntl is not None:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                fp.seek(0, os.SEEK_END)
                if fp.tell() == 0:
                    fp.write(b" ")
                    fp.flush()
                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            self._fp = fp
            self.acquired = True
            return True
        except OSError:
            try:
                fp.close()
            except OSError:
                pass
            return False

    def write_active_info(self, job: QueueJob) -> None:
        if not self.acquired or self._fp is None:
            return
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "job_id": job.job_id,
                "url": job.url,
                "convert_format": job.convert_format,
                "started_at": time.time(),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            # On Windows keep byte 0 locked; writing from offset 0 while locked is allowed by the lock holder.
            self._fp.seek(0)
            self._fp.truncate()
            self._fp.write(payload)
            self._fp.flush()
        except OSError:
            pass

    def release(self) -> None:
        if not self.acquired or self._fp is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            self._fp.close()
        except OSError:
            pass
        self._fp = None
        self.acquired = False

    def __enter__(self) -> "QueueLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def read_active_info(queue_dir: Path) -> dict | None:
    """Return metadata of the currently running job if the worker lock is held by another process."""
    probe = QueueLock(queue_dir)
    if probe.try_acquire():
        probe.release()
        return None
    lock_path = queue_dir / "active.lock"
    if not lock_path.is_file():
        return {"pid": None, "url": ""}
    try:
        raw = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"pid": None, "url": ""}


def list_pending_files(queue_dir: Path) -> list[Path]:
    pdir = _pending_dir(queue_dir)
    if not pdir.is_dir():
        return []
    return sorted(
        p for p in pdir.iterdir()
        if p.is_file() and p.suffix == ".json" and not p.name.startswith(".")
    )


def list_pending_jobs(queue_dir: Path) -> list[QueueJob]:
    jobs: list[QueueJob] = []
    for p in list_pending_files(queue_dir):
        job = _load_job_file(p)
        if job is not None:
            jobs.append(job)
    return jobs


def _load_job_file(path: Path) -> QueueJob | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("url"):
            return None
        return QueueJob(
            url=str(data["url"]),
            mode=str(data.get("mode") or "auto"),
            convert_format=str(data.get("convert_format") or "flac"),
            delete_original=bool(data.get("delete_original", True)),
            open_finder=bool(data.get("open_finder", True)),
            auto_tag=bool(data.get("auto_tag", True)),
            job_id=str(data.get("job_id") or ""),
            created_at=float(data.get("created_at") or 0.0),
        )
    except Exception:
        return None


def enqueue_job(queue_dir: Path, job: QueueJob) -> tuple[Path, int]:
    """Atomically append a QueueJob to `queue/pending/` and return (file_path, 1-based pending position)."""
    pdir = _pending_dir(queue_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    ts_us = int(time.time() * 1_000_000)
    final_path = pdir / f"{ts_us:018d}_{job.job_id}.json"
    tmp_path = pdir / f".tmp-{uuid.uuid4().hex[:8]}.json"
    tmp_path.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp_path), str(final_path))
    pending = list_pending_files(queue_dir)
    try:
        pos = pending.index(final_path) + 1
    except ValueError:
        pos = len(pending)
    return (final_path, pos)


def pop_next_job(queue_dir: Path) -> QueueJob | None:
    """Pop and return the oldest queued QueueJob (FIFO). Must only be called while holding QueueLock."""
    for p in list_pending_files(queue_dir):
        job = _load_job_file(p)
        try:
            p.unlink()
        except OSError:
            pass
        if job is not None:
            return job
    return None


def clear_pending_jobs(queue_dir: Path) -> int:
    """Remove all waiting jobs in `queue/pending/` (does not kill an active download)."""
    removed = 0
    for p in list_pending_files(queue_dir):
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


class SleepInhibitor:
    """Keep the machine awake while an overnight download queue is actively running (zero dependencies)."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._win_active = False

    def __enter__(self) -> "SleepInhibitor":
        if os.environ.get("SLSK_NO_CAFFEINATE") == "1":
            return self
        if sys.platform == "darwin":
            caff = shutil.which("caffeinate") or ("/usr/bin/caffeinate" if Path("/usr/bin/caffeinate").is_file() else None)
            if caff:
                try:
                    self._proc = subprocess.Popen(
                        [caff, "-i", "-w", str(os.getpid())],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError:
                    self._proc = None
        elif os.name == "nt":
            try:
                import ctypes

                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)  # type: ignore[attr-defined]
                self._win_active = True
            except Exception:
                pass
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._win_active and os.name == "nt":
            try:
                import ctypes

                ES_CONTINUOUS = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)  # type: ignore[attr-defined]
            except Exception:
                pass
            self._win_active = False
