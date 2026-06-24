"""
resource_limits.py

Apply OS-level resource limits to the child process before code execution.
Linux-only via the resource module; gracefully skipped on other platforms.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["apply_resource_limits", "is_linux"]

import platform
import sys


def is_linux() -> bool:
    return platform.system() == "Linux"


def apply_resource_limits(memory_limit_mb: int, cpu_limit_seconds: int):
    """
    Apply resource limits to the current process.
    Must be called early in child process before executing user code.

    - RLIMIT_AS: virtual memory cap
    - RLIMIT_CPU: CPU time cap (hard kill by OS)
    - RLIMIT_NPROC: prevent forking children
    - RLIMIT_FSIZE: prevent writing large files
    """
    if not is_linux():
        print(
            f"WARNING: resource limits not applied (platform={platform.system()})",
            file=sys.stderr,
        )
        return

    import resource

    memory_bytes = memory_limit_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_seconds, cpu_limit_seconds))

    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

    fsize_bytes = 10 * 1024 * 1024  # 10 MB
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_bytes, fsize_bytes))
