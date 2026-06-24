"""
runner.py

Async subprocess orchestration for sandbox code execution.
Spawns a child process per execution, enforces timeout via asyncio.wait_for,
kills the process group on expiry, and captures output.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["execute_code"]

import asyncio
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger
from app.executor.models import ExecutionRequest, ExecutionResponse, ExecutionStatus
from app.executor.sanitizer import sanitize_code, CodeSanitizationError


_CHILD_ENTRY = str(Path(__file__).resolve().parent / "child_entry.py")


async def execute_code(request: ExecutionRequest) -> ExecutionResponse:
    """
    Execute generated code in a child process with full isolation.

    1. Sanitize the code.
    2. Create a temp job directory with code and config files.
    3. Spawn a child process in a new process group.
    4. Enforce timeout with asyncio.wait_for.
    5. Kill process group on timeout.
    6. Capture and truncate stdout/stderr.
    7. Clean up temp directory.
    """
    start_time = time.monotonic()

    try:
        clean_code = sanitize_code(request.code, settings.max_code_bytes)
    except CodeSanitizationError as e:
        return ExecutionResponse(
            execution_id=request.execution_id,
            status=ExecutionStatus.FAILED,
            stderr=str(e),
            exit_code=1,
            duration_ms=0,
        )

    timeout = min(request.timeout_seconds, settings.max_timeout_seconds)
    job_dir = None

    try:
        job_dir = tempfile.mkdtemp(prefix="sandbox_job_")
        code_path = os.path.join(job_dir, "code.py")
        config_path = os.path.join(job_dir, "config.json")

        with open(code_path, "w") as f:
            f.write(clean_code)

        job_config = {
            "projectId": request.project_id,
            "timeoutSeconds": timeout,
            "memoryLimitMB": settings.memory_limit_mb,
            "cpuLimitBufferSeconds": settings.cpu_limit_buffer_seconds,
            "allowedHelpers": request.context.allowed_helpers,
        }
        with open(config_path, "w") as f:
            json.dump(job_config, f)

        kwargs = {}
        if sys.platform != "win32":
            kwargs["preexec_fn"] = os.setsid

        process = await asyncio.create_subprocess_exec(
            sys.executable, _CHILD_ENTRY, job_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            **kwargs,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout + settings.sync_wait_timeout_buffer_seconds,
            )
        except asyncio.TimeoutError:
            _kill_process_group(process)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

            duration_ms = int((time.monotonic() - start_time) * 1000)
            return ExecutionResponse(
                execution_id=request.execution_id,
                status=ExecutionStatus.TIMEOUT,
                stderr=f"Execution timed out after {timeout} seconds.",
                duration_ms=duration_ms,
                timed_out=True,
            )

        duration_ms = int((time.monotonic() - start_time) * 1000)

        stdout_str, truncated_stdout = _truncate_output(stdout_bytes, request.max_output_bytes)
        stderr_str, truncated_stderr = _truncate_output(stderr_bytes, request.max_output_bytes)
        truncated = truncated_stdout or truncated_stderr

        exit_code = process.returncode

        if exit_code == -9 or exit_code == 137:
            return ExecutionResponse(
                execution_id=request.execution_id,
                status=ExecutionStatus.MEMORY_LIMIT,
                stdout=stdout_str,
                stderr=f"Execution exceeded memory limit ({settings.memory_limit_mb} MB).",
                exit_code=exit_code,
                duration_ms=duration_ms,
                truncated=truncated,
            )

        if exit_code != 0:
            return ExecutionResponse(
                execution_id=request.execution_id,
                status=ExecutionStatus.FAILED,
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=exit_code,
                duration_ms=duration_ms,
                truncated=truncated,
            )

        return ExecutionResponse(
            execution_id=request.execution_id,
            status=ExecutionStatus.SUCCEEDED,
            stdout=stdout_str,
            stderr=stderr_str,
            exit_code=0,
            duration_ms=duration_ms,
            truncated=truncated,
        )

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(f"Execution runner error: {e}")
        return ExecutionResponse(
            execution_id=request.execution_id,
            status=ExecutionStatus.FAILED,
            stderr=f"Internal sandbox error: {type(e).__name__}",
            exit_code=1,
            duration_ms=duration_ms,
        )

    finally:
        if job_dir and os.path.exists(job_dir):
            _cleanup_job_dir(job_dir)


def _kill_process_group(process: asyncio.subprocess.Process):
    """Kill the entire process group to ensure no orphaned children."""
    try:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _truncate_output(data: bytes, max_bytes: int) -> tuple[str, bool]:
    """Decode and truncate output to max_bytes. Returns (string, was_truncated)."""
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = data.decode("latin-1")
    return text, truncated


def _cleanup_job_dir(job_dir: str):
    """Remove the temporary job directory."""
    import shutil
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:
        pass
