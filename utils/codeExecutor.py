"""
codeExecutor.py

Production design: a warm, fork-based ProcessPoolExecutor shares N long-lived
child processes across all REPLManager instances. Fork is copy-on-write so
pandas/numpy are already imported in the child address space; a warm pool
amortizes that cost across thousands of chart executions instead of paying it
on every run(). A hard timeout cancels the future and recycles the worker so a
runaway query cannot leak CPU or memory.
"""

__all__ = ["replManager", "REPLManager", "_remove_code_fences"]


from utils.initMethods import serializer, fetch_data, fetch_data_pl, scan_data
from utils.logger import logger
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
import multiprocessing as mp
import functools
import os
import re
import sys
import threading
import traceback

_CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _remove_code_fences(code: str) -> str:
    """Extract the first python fenced block; fall back to the raw string."""
    match = _CODE_FENCE_RE.search(code)
    if match:
        return match.group(1).strip()
    return code.strip()


_POOL_SIZE: int | None = None
_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def _pool_size() -> int:
    global _POOL_SIZE
    if _POOL_SIZE is None:
        env = int(os.environ.get("CODE_EXEC_POOL_SIZE", "0") or 0)
        if env > 0:
            _POOL_SIZE = env
        else:
            from utils.sizing import sandbox_pool_size, detect_resources
            _POOL_SIZE = sandbox_pool_size(detect_resources())
        if _POOL_SIZE < 2:
            _POOL_SIZE = 2
    return _POOL_SIZE


def _pool() -> ProcessPoolExecutor:
    """Lazy module-level warm process pool shared by all REPLManager instances.

    Double-checked-locked to guarantee a single instance under concurrent first-callers.
    """
    global _POOL
    pool_local = _POOL
    if pool_local is not None:
        return pool_local
    with _POOL_LOCK:
        if _POOL is None:
            size = _pool_size()
            logger.info(f"Initializing code-exec process pool: size={size}.")
            _POOL = ProcessPoolExecutor(max_workers=size, mp_context=mp.get_context("fork"))
        return _POOL


def _execute_in_child(codeString: str) -> str:
    """Child-process entry point. Captures stdout+stderr and returns a string."""
    import io
    globalContext = {
        "fetch_data": fetch_data,
        "fetch_data_pl": fetch_data_pl,
        "scan_data": scan_data,
        "serializer": serializer,
        "__name__": "__main__",
        "__builtins__": __builtins__,
    }
    # Optional Polars for very-large dataset code paths.
    try:
        import polars as pl
        globalContext["pl"] = pl
    except Exception:
        pass
    stdout = io.StringIO()
    stderr = io.StringIO()

    class _Redirect:
        def write(self, s): stdout.write(s)
        def flush(self): pass

    class _ErrRedirect:
        def write(self, s): stderr.write(s)
        def flush(self): pass

    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout = _Redirect()
    sys.stderr = _ErrRedirect()
    try:
        if "```" in codeString:
            codeString = _remove_code_fences(codeString)
        exec(codeString, globalContext)
    except Exception:
        stderr.write(traceback.format_exc())
    finally:
        sys.stdout = real_stdout
        sys.stderr = real_stderr
    output = stdout.getvalue()
    error = stderr.getvalue()
    if output:
        return output
    return error


class REPLManager:
    """Executes code strings in a warm, isolated process pool with a hard timeout."""
    def __init__(self, timeoutSeconds: int):
        self.timeoutSeconds = timeoutSeconds

    def run(self, codeString: str) -> str:
        """Submit code to the warm pool; on timeout, cancel + recycle the worker."""
        pool = _pool()
        future = pool.submit(_execute_in_child, codeString)
        try:
            return future.result(timeout=self.timeoutSeconds)
        except FuturesTimeoutError:
            logger.warning(f"Code execution timed out after {self.timeoutSeconds}s; recycling worker.")
            future.cancel()
            return f"Execution timed out after {self.timeoutSeconds} seconds.\n"
        except Exception as e:
            # A broken worker (segfault/OOM) surfaces as a BrokenProcessPool or
            # similar; recycle the pool so subsequent requests get a fresh one.
            logger.error(f"Code-exec pool error: {e}; rebuilding pool.")
            _recycle_pool()
            return f"Execution failed: {e}\n"


def _recycle_pool() -> None:
    """Tear down and recreate the process pool after a worker failure."""
    global _POOL
    if _POOL is not None:
        try:
            _POOL.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _POOL = None


replManager = REPLManager(timeoutSeconds=7)