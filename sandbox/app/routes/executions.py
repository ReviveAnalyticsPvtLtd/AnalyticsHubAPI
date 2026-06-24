"""
executions.py

Execution endpoints: single sync and batch sync.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["router"]

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.auth import verify_request
from app.core.concurrency import execution_pool, CapacityExhaustedError
from app.core.config import settings
from app.core.logging import logger
from app.executor.models import (
    ExecutionRequest,
    ExecutionResponse,
    ExecutionStatus,
    BatchExecutionRequest,
    BatchExecutionResponse,
    BatchItem,
)
from app.executor.runner import execute_code

router = APIRouter(prefix="/v1/executions", tags=["executions"])


@router.post("/sync")
async def execute_sync(request: Request):
    """
    Single synchronous code execution.
    Acquires a semaphore slot, spawns a child process, waits for result.
    """
    body = await verify_request(request)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    exec_request = ExecutionRequest(**payload)

    _validate_mode(exec_request.mode)

    try:
        await execution_pool.acquire()
    except CapacityExhaustedError:
        return JSONResponse(
            status_code=429,
            content=ExecutionResponse(
                execution_id=exec_request.execution_id,
                status=ExecutionStatus.REJECTED,
                error_code="capacity_exhausted",
                message="Sandbox execution capacity is exhausted. Try again later.",
            ).model_dump(by_alias=True, exclude_none=True),
        )

    try:
        result = await execute_code(exec_request)
    finally:
        await execution_pool.release()

    _log_execution(result, exec_request)

    return JSONResponse(
        content=result.model_dump(by_alias=True, exclude_none=True)
    )


@router.post("/batch-sync")
async def execute_batch_sync(request: Request):
    """
    Batch synchronous execution for dashboard widgets.
    Runs multiple executions concurrently within the sandbox semaphore.
    """
    body = await verify_request(request)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    batch_request = BatchExecutionRequest(**payload)

    if len(batch_request.executions) > settings.max_batch_size:
        return JSONResponse(
            status_code=400,
            content={"error": f"Batch size exceeds maximum ({settings.max_batch_size})"},
        )

    _validate_mode(batch_request.mode)

    start_time = time.monotonic()

    tasks = []
    for item in batch_request.executions:
        exec_request = ExecutionRequest(
            execution_id=item.execution_id,
            project_id=batch_request.project_id,
            user_id=batch_request.user_id,
            mode=batch_request.mode,
            code=item.code,
            timeout_seconds=item.timeout_seconds,
            max_output_bytes=item.max_output_bytes,
            context=batch_request.context,
            metadata=batch_request.metadata,
        )
        tasks.append(_execute_with_semaphore(exec_request))

    results = await asyncio.gather(*tasks)

    total_duration_ms = int((time.monotonic() - start_time) * 1000)

    batch_response = BatchExecutionResponse(
        batch_id=batch_request.batch_id,
        status="completed",
        total_duration_ms=total_duration_ms,
        results=list(results),
    )

    for result in results:
        _log_execution(result, None)

    return JSONResponse(
        content=batch_response.model_dump(by_alias=True, exclude_none=True)
    )


async def _execute_with_semaphore(exec_request: ExecutionRequest) -> ExecutionResponse:
    """Acquire semaphore, execute, release. On capacity exhaustion, return rejection."""
    try:
        await execution_pool.acquire()
    except CapacityExhaustedError:
        return ExecutionResponse(
            execution_id=exec_request.execution_id,
            status=ExecutionStatus.REJECTED,
            error_code="capacity_exhausted",
            message="Sandbox execution capacity is exhausted. Try again later.",
        )

    try:
        return await execute_code(exec_request)
    finally:
        await execution_pool.release()


_VALID_MODES = {"reporting_chart", "reporting_report", "dashboard_widget", "dashboard_filter"}


def _validate_mode(mode: str):
    if mode not in _VALID_MODES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode}. Must be one of {_VALID_MODES}")


def _log_execution(result: ExecutionResponse, request: ExecutionRequest | None):
    """Emit structured execution log."""
    log_data = {
        "event": "sandbox_execution_completed",
        "executionId": result.execution_id,
        "status": result.status.value if isinstance(result.status, ExecutionStatus) else result.status,
        "durationMs": result.duration_ms,
        "timedOut": result.timed_out,
        "stdoutBytes": len(result.stdout.encode()) if result.stdout else 0,
        "stderrBytes": len(result.stderr.encode()) if result.stderr else 0,
        "truncated": result.truncated,
    }
    if request:
        log_data["projectId"] = request.project_id
        log_data["mode"] = request.mode
    logger.bind(structured=True).info(json.dumps(log_data))
