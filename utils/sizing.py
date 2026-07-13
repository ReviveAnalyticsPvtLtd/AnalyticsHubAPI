"""
sizing.py — server-aware auto-sizing for the API worker, sandbox pool, and DataFrame cache.

Replaces hardcoded constants with formulas derived from CPU count, total/free RAM,
and Docker/cgroup-imposed limits. Every knob here can be overridden via env so
operators can pin values explicitly in production.

Heuristics (tuned for a typical NubrixAI deployment):

  - Sandbox pool size
      Each sandbox process can peak several GB while executing a user query
      (groupby on a large frame briefly duplicates the frame). Cap so the pool
      never exceeds (free_MEM_bytes // SANDBOX_RESERVE_gb), but never below 2.
      CPU-wise, oversubscribe up to 2× cpu_count since sandboxes spend most time
      waiting on Gemini I/O.

  - DataFrame cache budget per worker
      Leave ~reserves for OS, Python interpreter, sandbox working set, and
      LangChain. Spend the remainder on the cache. Bound by DF_CACHE_HARD_CEILING
      so a very large box doesn't blow a 64GB+ allocation into a single worker.

  - Gunicorn optimal workers
      The fastapi process holds one process pool, one LRU cache, and one LLM
      client cache; size so the per-API-worker memory pool is sane.
"""

__version__ = "1.0.0"
__all__ = [
    "detect_resources",
    "sandbox_pool_size",
    "dataframe_cache_bytes",
    "dataframe_cache_entries",
    "gunicorn_workers",
    "parallel_chart_workers",
]


import math
import os
import psutil


# Worker reserves that cannot be used by the cache.
# Sizes are deliberately conservative: a sandbox running a ``groupby`` on a
# 1 GiB frame briefly peaks ~1.2 GiB. The pool only runs as many sandboxes as
# its pool cap, and a fraction of those are active at any given instant.
_PY_INTERPRETER_RESERVE_BYTES = 400 * 1024 * 1024          # 400 MiB Python interpreter + LangChain
_SANDBOX_PEAK_RESERVE_BYTES = int(os.environ.get("SIZING_SANDBOX_PEAK_BYTES", str(750 * 1024 * 1024)))  # 750 MiB / sandbox
_HARD_CEILING = int(os.environ.get("DF_CACHE_HARD_CEILING_BYTES", str(6 * 1024 * 1024 * 1024)))  # 6 GiB
_SANDBOX_RESERVE_GB = float(os.environ.get("SIZING_SANDBOX_RESERVE_GB", "0.75"))
_MIN_CACHE_BYTES = int(os.environ.get("DF_CACHE_MIN_BYTES", str(256 * 1024 * 1024)))  # 256 MiB floor


def detect_resources() -> dict:
    """Return a dict of detected host resources honoring cgroup limits."""
    cpu_count = os.cpu_count() or 4
    mem = psutil.virtual_memory()
    # cgroup memory limit (Docker / K8s) is the real cap; fall back to host total.
    cgroup_limit = _cgroup_memory_limit_bytes()
    total = cgroup_limit or mem.total
    available = min(mem.available, total) if cgroup_limit else mem.available
    return {
        "cpu_count": cpu_count,
        "total_memory_bytes": int(total),
        "available_memory_bytes": int(available),
        "cgroup_limit": bool(cgroup_limit),
    }


def _cgroup_memory_limit_bytes() -> int | None:
    """Read cgroup v1/v2 memory limit; returns None if uncontrolled."""
    paths = [
        "/sys/fs/cgroup/memory.max",                 # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
    ]
    for path in paths:
        try:
            with open(path) as f:
                value = int(f.read().strip())
                # cgroup v2 reports "max" for unlimited
                if value > 0 and value < (1 << 62):
                    return value
        except (FileNotFoundError, ValueError):
            continue
    return None


def sandbox_pool_size(resources: dict | None = None) -> int:
    """Compute a safe sandbox-pool size.

    The sandbox fork is destructive memory-wise for big-frame queries, so the
    pool is bounded by both CPU (oversub 2x) and free RAM.
    """
    r = resources or detect_resources()
    env_override = int(os.environ.get("CODE_EXEC_POOL_SIZE", "0") or 0)
    if env_override > 0:
        # Make sure the explicit override is at least 1 worker.
        return max(1, min(env_override, 64))
    cpu_cap = r["cpu_count"] * 2
    ram_cap = int(r["available_memory_bytes"] // int(_SANDBOX_RESERVE_GB * 1024 * 1024 * 1024))
    return max(2, min(cpu_cap, ram_cap, 32))


def dataframe_cache_bytes(workers: int, resources: dict | None = None) -> int:
    """Compute a per-worker DataFrame cache budget honoring total RAM.

    Reserves memory for the Python interpreter of every API worker plus one
    concurrent peak per sandbox pool (the rest are idle waiting on Gemini I/O
    most of the time). The remainder is split across API workers for their
    DataFrame caches. A 256 MiB per-worker floor keeps the cache useful even
    on lean boxes.
    """
    r = resources or detect_resources()
    env_override = int(os.environ.get("DF_CACHE_MAX_BYTES", "0") or 0)
    if env_override > 0:
        return env_override
    total = int(r["total_memory_bytes"])
    interpreter_per_worker = _PY_INTERPRETER_RESERVE_BYTES * workers
    sandbox_peak = _SANDBOX_PEAK_RESERVE_BYTES * sandbox_pool_size(resources)
    safety = int(total * 0.10)
    total_reserves = safety + sandbox_peak + interpreter_per_worker
    budget = max(_MIN_CACHE_BYTES * workers, total - total_reserves)
    per_worker = max(_MIN_CACHE_BYTES, budget // max(1, workers))
    return min(per_worker, _HARD_CEILING)


def dataframe_cache_entries(resources: dict | None = None) -> int:
    """Default 128 entries per worker; tunable via env."""
    env = int(os.environ.get("DF_CACHE_MAX_ENTRIES", "0") or 0)
    if env > 0:
        return env
    cpu = (resources or detect_resources())["cpu_count"]
    return max(32, min(512, cpu * 8))


def gunicorn_workers(resources: dict | None = None) -> int:
    """Recommended gunicorn worker count for the box.

    FastAPI/gunicorn rule-of-thumb: ``(2 * cores) + 1``. We bound by both that
    formula and RAM (1.5 GB per API worker including interpreter + sandbox pool +
    cache budget) so big boxes don't overprovision workers via either limit.
    """
    env = int(os.environ.get("WEB_WORKERS", "0") or 0)
    if env > 0:
        return env
    r = resources or detect_resources()
    cpu = r["cpu_count"]
    available_gb = r["available_memory_bytes"] / (1024 ** 3)
    # Gunicorn FastAPI+uvicorn rule of thumb: workers ≈ 2× cores, with a meaningful
    # floor so even a 2-core box has multiple workers for I/O overlap.
    by_cpu = max(2, 2 * cpu + 1)
    by_ram = max(2, int(available_gb // 1.5))
    return min(64, min(by_cpu, by_ram))


def parallel_chart_workers(resources: dict | None = None) -> int:
    """Per-request parallelism for ``generateChartsInParallel``.

    Charts are I/O bound at the LLM + Redis layer; oversubscribe somewhat beyond
    cpu_count, but cap to keep the LLM API rate-limiter happy.
    """
    env = int(os.environ.get("PARALLEL_CHART_WORKERS", "0") or 0)
    if env > 0:
        return env
    r = resources or detect_resources()
    return min(16, max(4, r["cpu_count"]))