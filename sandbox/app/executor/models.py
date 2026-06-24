"""
models.py

Pydantic models for sandbox execution requests and responses.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "ExecutionRequest",
    "ExecutionResponse",
    "BatchExecutionRequest",
    "BatchExecutionResponse",
    "BatchItem",
    "ExecutionStatus",
]

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    MEMORY_LIMIT = "memory_limit"
    REJECTED = "rejected"


class ExecutionContext(BaseModel):
    allowed_helpers: list[str] = Field(default_factory=lambda: ["fetch_data", "serializer"])


class ExecutionMetadata(BaseModel):
    source: str = ""
    request_path: str = Field(default="", alias="requestPath")

    model_config = {"populate_by_name": True}


class ExecutionRequest(BaseModel):
    execution_id: str = Field(alias="executionId")
    project_id: str = Field(alias="projectId")
    user_id: str = Field(alias="userId")
    mode: str
    language: str = "python"
    code: str
    timeout_seconds: int = Field(default=7, alias="timeoutSeconds")
    max_output_bytes: int = Field(default=262144, alias="maxOutputBytes")
    context: ExecutionContext = Field(default_factory=ExecutionContext)
    metadata: ExecutionMetadata = Field(default_factory=ExecutionMetadata)

    model_config = {"populate_by_name": True}


class ExecutionResponse(BaseModel):
    execution_id: str = Field(alias="executionId")
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = Field(default=None, alias="exitCode")
    duration_ms: int = Field(default=0, alias="durationMs")
    timed_out: bool = Field(default=False, alias="timedOut")
    truncated: bool = False
    error_code: Optional[str] = Field(default=None, alias="errorCode")
    message: Optional[str] = None

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class BatchItem(BaseModel):
    execution_id: str = Field(alias="executionId")
    code: str
    timeout_seconds: int = Field(default=7, alias="timeoutSeconds")
    max_output_bytes: int = Field(default=262144, alias="maxOutputBytes")

    model_config = {"populate_by_name": True}


class BatchExecutionRequest(BaseModel):
    batch_id: str = Field(alias="batchId")
    project_id: str = Field(alias="projectId")
    user_id: str = Field(alias="userId")
    mode: str
    executions: list[BatchItem]
    context: ExecutionContext = Field(default_factory=ExecutionContext)
    metadata: ExecutionMetadata = Field(default_factory=ExecutionMetadata)

    model_config = {"populate_by_name": True}


class BatchExecutionResponse(BaseModel):
    batch_id: str = Field(alias="batchId")
    status: str = "completed"
    total_duration_ms: int = Field(default=0, alias="totalDurationMs")
    results: list[ExecutionResponse]

    model_config = {"populate_by_name": True, "serialize_by_alias": True}
