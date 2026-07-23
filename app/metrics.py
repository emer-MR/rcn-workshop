import os
import time
from threading import Lock

import psutil

_PROC = psutil.Process(os.getpid())
_LOCK = Lock()
_UPLOADS: list[dict] = []
_MAX_HISTORY = 50


def record_upload(file_name: str, file_size_bytes: int, rows: int, duration_s: float) -> None:
    entry = {
        "timestamp": time.time(),
        "file": file_name,
        "size_bytes": file_size_bytes,
        "rows": rows,
        "duration_s": round(duration_s, 3),
        "throughput_rows_per_s": round(rows / duration_s, 1) if duration_s > 0 else None,
    }
    with _LOCK:
        _UPLOADS.append(entry)
        if len(_UPLOADS) > _MAX_HISTORY:
            del _UPLOADS[: len(_UPLOADS) - _MAX_HISTORY]


def snapshot() -> dict:
    with _LOCK:
        uploads = list(_UPLOADS)
    mem = _PROC.memory_info()
    return {
        "process": {
            "rss_mb": round(mem.rss / 1024 / 1024, 1),
            "cpu_percent": _PROC.cpu_percent(interval=None),
            "num_threads": _PROC.num_threads(),
            "uptime_s": round(time.time() - _PROC.create_time(), 1),
        },
        "system": {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_available_mb": round(psutil.virtual_memory().available / 1024 / 1024, 1),
        },
        "uploads": uploads[-20:],
    }
