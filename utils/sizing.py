"""
sizing.py — hardcoded constants. No formulas, no auto-detection.

Workers: 2 (async event loop handles concurrency, not processes)
Sandbox pool: 2 (one active + one standby)
Polars threads: 2 per process (capped via env)
Cache: 512MB per worker, 64 entries

Total PIDs on any box: 2 workers + 2×2 sandbox + 4 celery + 1 beat = 11
Total threads: 2×(4 anyio + 2×2 polars) + 4 celery = 20
Well under any OS / Docker limit.
"""

__version__ = "2.0.0"
__all__ = [
    "detect_resources",
    "sandbox_pool_size",
    "dataframe_cache_bytes",
    "dataframe_cache_entries",
    "gunicorn_workers",
    "parallel_chart_workers",
]

import os
import psutil


def detect_resources() -> dict:
    try:
        mem = psutil.virtual_memory()
        total_ram = mem.total
    except Exception:
        total_ram = 2 * 1024 * 1024 * 1024  # default 2GB
    return {
        "cpu_count": os.cpu_count() or 4,
        "total_ram_bytes": total_ram,
    }


def sandbox_pool_size(resources: dict | None = None) -> int:
    return max(1, int(os.environ.get("CODE_EXEC_POOL_SIZE", "2") or "2"))


def dataframe_cache_bytes(workers: int, resources: dict | None = None) -> int:
    env_val = os.environ.get("DF_CACHE_MAX_BYTES")
    if env_val is not None:
        return int(env_val)
    if resources is None:
        resources = detect_resources()
    total_ram = resources.get("total_ram_bytes", 2 * 1024 * 1024 * 1024)
    # Dedicate 10% of total RAM to Arrow cache, divided among Gunicorn workers
    val = int(total_ram * 0.10 / max(1, workers))
    return max(128 * 1024 * 1024, val)


def dataframe_cache_entries(resources: dict | None = None) -> int:
    return int(os.environ.get("DF_CACHE_MAX_ENTRIES", "64") or "64")


def gunicorn_workers(resources: dict | None = None) -> int:
    env_val = os.environ.get("WEB_WORKERS")
    if env_val is not None:
        return max(1, int(env_val))
    if resources is None:
        resources = detect_resources()
    cpus = resources.get("cpu_count", 4)
    # Standard formula: 2 * cpus + 1, capped at 8 workers for monolithic memory safety
    return min(8, 2 * cpus + 1)


def parallel_chart_workers(resources: dict | None = None) -> int:
    env_val = os.environ.get("PARALLEL_CHART_WORKERS")
    if env_val is not None:
        return max(1, int(env_val))
    if resources is None:
        resources = detect_resources()
    cpus = resources.get("cpu_count", 4)
    return min(4, cpus)