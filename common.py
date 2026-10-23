import json
import math
import re
import subprocess
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_operation_guard = ContextVar("operation_guard", default=None)


@contextmanager
def operation_guard(check):
    token = _operation_guard.set(check)
    try:
        yield
    finally:
        _operation_guard.reset(token)


def check_operation():
    check = _operation_guard.get()
    if check:
        # A guard's own ADB reads must not recursively call the guard.
        with operation_guard(None):
            check()
    return check is not None


def parallel(function, items, workers=4):
    """Run blocking calls side by side, results in order. TV ownership checks stay on
    this thread (SQLite, ADB); a cancelled batch kills its workers' processes."""
    cancelled = threading.Event()

    def stop():
        if cancelled.is_set():
            raise RuntimeError("Cancelled with its batch")

    def call(item):
        with operation_guard(stop):
            return function(item)

    with ThreadPoolExecutor(max(1, min(workers, len(items)))) as pool:
        futures = [pool.submit(call, item) for item in items]
        try:
            pending = set(futures)
            while pending:
                check_operation()
                pending = wait(pending, timeout=.25).not_done
        finally:
            cancelled.set()
    return [f.result() for f in futures]


def communicate_checked(process, timeout):
    """Allow TV ownership checks to cancel downloads, including their child processes."""
    deadline = time.monotonic() + timeout
    completed = False
    try:
        while True:
            guarded = check_operation()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            try:
                output = process.communicate(timeout=min(.25, remaining) if guarded else remaining)
                completed = True
                return output
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise
    finally:
        if not completed:
            # The parent may have exited while descendants still hold its pipes open.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artists: tuple[str, ...]
    duration: float
    album: str = ""

    def __post_init__(self):
        if (not re.fullmatch(r"[A-Za-z0-9]{22}", self.id)
                or not self.title.strip() or not self.artists
                or not all(a.strip() for a in self.artists)
                or not math.isfinite(self.duration) or self.duration <= 0):
            raise ValueError("Incomplete Spotify metadata or non-music item")


def load_config():
    cfg = json.loads((ROOT / "config.example.json").read_text())
    path = ROOT / "config.json"
    if path.exists():
        custom = json.loads(path.read_text())
        if set(custom) - set(cfg):
            raise ValueError(f"Unknown config options: {set(custom) - set(cfg)}")
        cfg.update(custom)
    if cfg["spotify_source"] not in {"tv", "auto", "macos", "web"}:
        raise ValueError("spotify_source must be tv, auto, macos or web")
    if not re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]{1,5}", cfg["tv_target"]):
        raise ValueError("tv_target must be host:port")
    if not 0 < int(cfg["tv_target"].rsplit(":", 1)[1]) < 65536:
        raise ValueError("Invalid ADB port")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", cfg["smarttube_package"]):
        raise ValueError("Invalid Android package name")
    for name, low, high in [
        ("poll_seconds", 1, 120), ("min_score", 0, 200),
        ("max_duration_delta_seconds", 1, 120),
        ("max_duration_delta_ratio", .01, .5), ("search_results", 3, 30),
        ("search_timeout_seconds", 5, 120), ("adb_timeout_seconds", 2, 60),
        ("matching_timeout_seconds", 30, 600), ("audio_timeout_seconds", 10, 120),
    ]:
        value = cfg[name]
        if type(value) not in {int, float} or not low <= value <= high:
            raise ValueError(f"{name}: expected a value between {low} and {high}")
    if type(cfg["search_results"]) is not int:
        raise ValueError("search_results must be an integer")
    for name in ("prefer_official", "allow_audio_fallback"):
        if type(cfg[name]) is not bool:
            raise ValueError(f"{name} must be a boolean")
    return cfg


def run(args, timeout=12):
    check_operation()
    try:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace", start_new_session=True) as process:
            stdout, stderr = communicate_checked(process, timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timeout ({timeout}s): {Path(args[0]).name}") from exc
    if process.returncode:
        raise RuntimeError(f"{Path(args[0]).name}: {(stderr or stdout).strip()[:600]}")
    return stdout
