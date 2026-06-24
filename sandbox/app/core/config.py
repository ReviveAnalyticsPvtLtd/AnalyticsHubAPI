"""
config.py

Sandbox configuration loader. Reads defaults from sandbox.defaults.json,
then applies environment variable overrides, producing a validated settings object.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["settings", "SandboxSettings"]

import json
import os
from pathlib import Path
from pydantic import BaseModel, field_validator


_CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "sandbox.defaults.json"


class SandboxSettings(BaseModel):
    max_concurrent_executions: int = 8
    max_queue_depth: int = 20
    default_timeout_seconds: int = 7
    max_timeout_seconds: int = 30
    max_code_bytes: int = 51200
    max_output_bytes: int = 262144
    max_batch_size: int = 12
    sync_wait_timeout_buffer_seconds: int = 5
    memory_limit_mb: int = 512
    cpu_limit_buffer_seconds: int = 2
    replay_window_seconds: int = 60

    sandbox_shared_secret: str = ""
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    file_url: str = ""
    port: int = 8000

    @field_validator("sandbox_shared_secret")
    @classmethod
    def secret_must_be_set(cls, v: str) -> str:
        if not v:
            raise ValueError("SANDBOX_SHARED_SECRET must be set")
        return v


def _load_settings() -> SandboxSettings:
    defaults = {}
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE) as f:
            defaults = json.load(f)

    field_map = {
        "maxConcurrentExecutions": "max_concurrent_executions",
        "maxQueueDepth": "max_queue_depth",
        "defaultTimeoutSeconds": "default_timeout_seconds",
        "maxTimeoutSeconds": "max_timeout_seconds",
        "maxCodeBytes": "max_code_bytes",
        "maxOutputBytes": "max_output_bytes",
        "maxBatchSize": "max_batch_size",
        "syncWaitTimeoutBufferSeconds": "sync_wait_timeout_buffer_seconds",
        "memoryLimitMB": "memory_limit_mb",
        "cpuLimitBufferSeconds": "cpu_limit_buffer_seconds",
        "replayWindowSeconds": "replay_window_seconds",
    }

    kwargs = {}
    for json_key, py_key in field_map.items():
        if json_key in defaults:
            kwargs[py_key] = defaults[json_key]

    env_overrides = {
        "SANDBOX_MAX_CONCURRENT_EXECUTIONS": "max_concurrent_executions",
        "SANDBOX_MAX_QUEUE_DEPTH": "max_queue_depth",
        "SANDBOX_DEFAULT_TIMEOUT_SECONDS": "default_timeout_seconds",
        "SANDBOX_MAX_TIMEOUT_SECONDS": "max_timeout_seconds",
        "SANDBOX_MAX_CODE_BYTES": "max_code_bytes",
        "SANDBOX_MAX_OUTPUT_BYTES": "max_output_bytes",
        "SANDBOX_MAX_BATCH_SIZE": "max_batch_size",
        "SANDBOX_SYNC_WAIT_TIMEOUT_BUFFER_SECONDS": "sync_wait_timeout_buffer_seconds",
        "SANDBOX_MEMORY_LIMIT_MB": "memory_limit_mb",
        "SANDBOX_CPU_LIMIT_BUFFER_SECONDS": "cpu_limit_buffer_seconds",
        "SANDBOX_REPLAY_WINDOW_SECONDS": "replay_window_seconds",
        "SANDBOX_SHARED_SECRET": "sandbox_shared_secret",
        "REDIS_HOST": "redis_host",
        "REDIS_PORT": "redis_port",
        "REDIS_PASSWORD": "redis_password",
        "FILE_URL": "file_url",
        "PORT": "port",
    }

    for env_key, py_key in env_overrides.items():
        val = os.environ.get(env_key)
        if val is not None:
            kwargs[py_key] = val

    return SandboxSettings(**kwargs)


settings = _load_settings()
