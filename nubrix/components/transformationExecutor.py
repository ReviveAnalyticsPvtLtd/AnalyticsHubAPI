"""
transformationExecutor.py

This module executes generated transformation code and persists approved
transformed tables.

Production design: every code execution runs in a forked child process so a
hard timeout can SIGKILL it. This prevents runaway pandas/numpy work from
hanging the agent's self-validation loop or OOMing the API worker. The sandbox
globals are built in the child, so the parent never holds the transformed
DataFrame in memory except as parquet bytes.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["TransformationExecutor"]


from utils.exceptionHandler import CustomException
from utils.initMethods import fetch_data, fetch_data_pl, scan_data, serializer
from api.commons import client
from utils.logger import logger
import multiprocessing as mp
import pandas as pd
import numpy as np
import datetime
import redis
import json
import math
import io
import os
import re

# Module-level Redis connection pool for reuse
_redis_pool: redis.ConnectionPool | None = None


def _get_redis_pool() -> redis.ConnectionPool:
    """Get or create a shared Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            password=os.environ["REDIS_PASSWORD"],
            max_connections=10,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=10,
        )
    return _redis_pool


_ALLOWED_MODULES = frozenset({"pandas", "numpy", "datetime", "math", "re", "time", "sklearn", "scipy", "statsmodels", "polars", "polars.select"})
_DANGEROUS_BUILTINS = frozenset({
    "open", "compile", "eval", "exec", "globals", "locals", "memoryview",
    "input", "breakpoint", "exit", "quit", "help", "license", "copyright",
    "credit", "__import__",
})


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Allow only transformation-safe imports."""
    rootName = name.split(".")[0]
    if rootName not in _ALLOWED_MODULES:
        raise ImportError(f"Import '{name}' is not allowed in transformation code.")
    return __import__(name, globals, locals, fromlist, level)


def _build_safe_builtins() -> dict:
    """Return a constrained builtins map for generated code."""
    import builtins
    safe = {k: v for k, v in builtins.__dict__.items() if k not in _DANGEROUS_BUILTINS}
    safe["__import__"] = _restricted_import
    return safe


def _sandbox_globals(projectId: str) -> dict:
    """Build the execution globals for a transformation/inspection run."""
    g = {
        "__builtins__": _build_safe_builtins(),
        "datetime": datetime,
        "fetch_data": fetch_data,
        "fetch_data_pl": fetch_data_pl,
        "scan_data": scan_data,
        "math": math,
        "np": np,
        "pd": pd,
        "projectId": projectId,
        "serializer": serializer,
    }
    try:
        import polars as pl  # lazy import keeps parent process lean if unused
        g["pl"] = pl
        # Polars read of a pandas DataFrame is the fastest bridge for sandbox code.
        # The LLM can use ``pl.DataFrame(df)`` to upgrade speed if desired.
    except Exception:
        pass
    return g


def _child_execute_validate(projectId: str, pythonCode: str, out_q: mp.Queue, err_q: mp.Queue) -> None:
    """Child entry: exec the code and put final_df rows on a queue as parquet bytes."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    g = _sandbox_globals(projectId)
    import sys
    class _W:
        def write(self, s): stdout.write(s)
        def flush(self): pass
    class _E:
        def write(self, s): stderr.write(s)
        def flush(self): pass
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _W(), _E()
    try:
        exec(pythonCode, g)
        finalDf = g.get("final_df")
        if finalDf is None:
            err_q.put("Transformation code must create a final_df variable.")
            return
        # Accept pandas DataFrame, Polars DataFrame, or Polars LazyFrame.
        # All are normalized to an Arrow table for parquet serialization so the
        # sandbox can use whichever engine is fastest for the workload.
        try:
            import polars as pl
        except Exception:
            pl = None
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except Exception:
            pa = None
            pq = None
        if pl is not None and isinstance(finalDf, pl.LazyFrame):
            finalDf = finalDf.collect()
        if pl is not None and isinstance(finalDf, pl.DataFrame):
            try:
                arrow_table = finalDf.to_arrow()
            except Exception:
                # Fallback: polars → pandas → arrow
                arrow_table = pa.Table.from_pandas(finalDf.to_pandas(), preserve_index=False) if pa else None
        elif isinstance(finalDf, pd.DataFrame):
            arrow_table = pa.Table.from_pandas(finalDf, preserve_index=False) if pa else None
        else:
            err_q.put(f"final_df must be a pandas/polars DataFrame or LazyFrame, got {type(finalDf).__name__}.")
            return
        buf = io.BytesIO()
        if pq is not None and arrow_table is not None:
            pq.write_table(arrow_table, buf, compression="snappy")
        else:
            # Fallback to pandas parquet writer
            df_to_write = arrow_table.to_pandas() if arrow_table is not None else (
                finalDf.to_pandas() if pl is not None and isinstance(finalDf, pl.DataFrame) else finalDf
            )
            df_to_write.to_parquet(buf, compression="snappy")
        out_q.put(buf.getvalue())
    except Exception:
        import traceback
        err_q.put(traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        try:
            out_q.put(None)
        except Exception:
            pass


def _child_execute_inspect(projectId: str, pythonCode: str, out_q: mp.Queue, err_q: mp.Queue) -> None:
    """Child entry: exec inspection code and put combined stdout+stderr string."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    g = _sandbox_globals(projectId)
    import sys
    class _W:
        def write(self, s): stdout.write(s)
        def flush(self): pass
    class _E:
        def write(self, s): stderr.write(s)
        def flush(self): pass
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _W(), _E()
    try:
        exec(pythonCode, g)
    except Exception:
        import traceback
        stderr.write(traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        out_q.put(stdout.getvalue())
        err_q.put(stderr.getvalue())


class TransformationExecutor:
    """
    Execute pandas transformation code and persist approved transformed tables.
    """
    def __init__(self, timeoutSeconds: int = 30):
        """Initialize the executor."""
        self.timeoutSeconds = timeoutSeconds
        self.client = client
        self._ctx = mp.get_context("fork")

    def _redis_client(self) -> redis.Redis:
        """Create a Redis client using shared connection pool."""
        return redis.Redis(connection_pool=_get_redis_pool())

    def _validate_table_name(self, tableName: str) -> str:
        """Validate the output table name."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", tableName):
            raise ValueError("Table name must start with a letter and contain only letters, numbers, hyphens, and underscores.")
        return tableName

    def _run_child(self, target, *args) -> tuple[object, object]:
        """Spawn a child process, enforce a hard timeout, SIGKILL on overrun."""
        out_q = self._ctx.Queue()
        err_q = self._ctx.Queue()
        proc = self._ctx.Process(target=target, args=(*args, out_q, err_q), daemon=False)
        proc.start()
        proc.join(timeout=self.timeoutSeconds)
        if proc.is_alive():
            logger.warning(f"Transformation child timed out after {self.timeoutSeconds}s; killing PID {proc.pid}.")
            proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=1)
            try:
                out_q.close(); err_q.close()
            except Exception:
                pass
            raise TimeoutError(f"Transformation execution exceeded {self.timeoutSeconds} seconds.")
        out = None
        err = None
        try:
            out = out_q.get_nowait()
        except Exception:
            pass
        try:
            err = err_q.get_nowait()
        except Exception:
            pass
        try:
            out_q.close(); err_q.close()
        except Exception:
            pass
        return out, err

    def _execute_code(self, projectId: str, pythonCode: str) -> pd.DataFrame:
        """Execute generated code and return `final_df` (validation run, result discarded)."""
        parquet_bytes, err = self._run_child(_child_execute_validate, projectId, pythonCode)
        if err:
            raise ValueError(str(err))
        if parquet_bytes is None:
            raise ValueError("Transformation produced no final_df.")
        return pd.read_parquet(io.BytesIO(parquet_bytes))

    def executeInspection(self, projectId: str, pythonCode: str) -> str:
        """
        Execute inspection code in the sandbox and return stdout + stderr.
        """
        out, err = self._run_child(_child_execute_inspect, projectId, pythonCode)
        output = out or ""
        errors = err or ""
        if errors:
            return f"Stdout:\n{output}\nStderr/Errors:\n{errors}"
        return output if output else "Inspection executed successfully with no output."

    def executeAndPreview(self, projectId: str, pythonCode: str, tableName: str) -> tuple[list[dict], bytes]:
        """
        Execute code and return preview rows plus parquet bytes.
        """
        try:
            self._validate_table_name(tableName)
            parquetBytes, err = self._run_child(_child_execute_validate, projectId, pythonCode)
            if err:
                raise ValueError(str(err))
            if parquetBytes is None:
                raise ValueError("Transformation produced no final_df.")
            finalDf = pd.read_parquet(io.BytesIO(parquetBytes))
            previewRecords = finalDf.head(100).replace({np.nan: None}).to_dict(orient="records")
            previewRows = json.loads(json.dumps(previewRecords, default=serializer))
            return previewRows, parquetBytes
        except TimeoutError:
            raise
        except Exception as e:
            exception = CustomException(e, statusCode=400, uiMessage=str(e))
            logger.error(exception)
            raise exception

    def apply(self, projectId: str, parquetBytes: bytes, tableName: str) -> None:
        """
        Upload parquet bytes to Supabase storage and refresh the fetch_data cache.
        Invalidates the in-process DataFrame LRU + Redis byte cache so the next
        fetch_data call sees the new table immediately.
        """
        try:
            self._validate_table_name(tableName)
            storagePath = f"{projectId}/{tableName}.parquet"
            self.client.storage.from_("AnalyticsHub").upload(
                path=storagePath,
                file=parquetBytes,
                file_options={"upsert": "true"},
            )
            redisClient = self._redis_client()
            redisClient.set(name=f"{projectId}::{tableName}", value=parquetBytes, ex=300)
            # Drop the stale in-process DataFrame so reporting/transformation see the new data.
            try:
                from utils.initMethods import invalidate_data_cache
                invalidate_data_cache(projectId=projectId, tableName=tableName)
            except Exception:
                pass
        except Exception as e:
            exception = CustomException(e, statusCode=500, uiMessage="Failed to apply transformation.")
            logger.error(exception)
            raise exception