"""
sandboxClient.py

Main backend client for calling the sandbox execution service.
Handles HMAC signing, circuit breaker with automatic local fallback,
single and batch execution, and result mapping to REPLManager-compatible output.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["sandbox_client", "SandboxClient"]

import hashlib
import hmac
import json
import os
import time
import uuid
from enum import Enum
from threading import Lock
from typing import Optional

import httpx

from utils.logger import logger
from utils.codeExecutor import replManager


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitBreaker:
    """
    Circuit breaker for sandbox calls.
    Opens after threshold failures within a window, then probes after recovery period.
    """

    def __init__(
        self,
        threshold: int = 5,
        window_seconds: int = 60,
        recovery_seconds: int = 30,
    ):
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._recovery_seconds = recovery_seconds
        self._state = CircuitState.CLOSED
        self._failures: list[float] = []
        self._last_open_time: float = 0
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_open_time >= self._recovery_seconds:
                    self._state = CircuitState.HALF_OPEN
                    logger.warning("Circuit breaker: OPEN -> HALF_OPEN (probing sandbox)")
            return self._state

    def record_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failures.clear()
                logger.info("Circuit breaker: HALF_OPEN -> CLOSED (sandbox recovered)")
            elif self._state == CircuitState.CLOSED:
                pass

    def record_failure(self):
        with self._lock:
            now = time.time()
            self._failures = [t for t in self._failures if now - t < self._window_seconds]
            self._failures.append(now)

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_open_time = now
                logger.warning("Circuit breaker: HALF_OPEN -> OPEN (probe failed)")
            elif self._state == CircuitState.CLOSED and len(self._failures) >= self._threshold:
                self._state = CircuitState.OPEN
                self._last_open_time = now
                logger.warning(
                    f"Circuit breaker: CLOSED -> OPEN "
                    f"({len(self._failures)} failures in {self._window_seconds}s)"
                )

    @property
    def allow_request(self) -> bool:
        state = self.state
        return state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)


class SandboxClient:
    """
    Client for the sandbox execution service.
    Signs requests with HMAC, handles timeouts, and implements circuit breaker fallback.
    """

    def __init__(self):
        self._sandbox_url = os.environ.get("SANDBOX_URL", "http://sandbox-execution.railway.internal:8000")
        self._shared_secret = os.environ.get("SANDBOX_SHARED_SECRET", "")
        self._execution_backend = os.environ.get("CODE_EXECUTION_BACKEND", "local")

        threshold = int(os.environ.get("CIRCUIT_BREAKER_THRESHOLD", "5"))
        window = int(os.environ.get("CIRCUIT_BREAKER_WINDOW_SECONDS", "60"))
        recovery = int(os.environ.get("CIRCUIT_BREAKER_RECOVERY_SECONDS", "30"))
        self._circuit = _CircuitBreaker(threshold, window, recovery)

        self._timeout_buffer = int(os.environ.get("SANDBOX_SYNC_WAIT_TIMEOUT_BUFFER_SECONDS", "5"))

    @property
    def is_sandbox_mode(self) -> bool:
        return self._execution_backend == "sandbox"

    def run(self, code: str, project_id: str = "", mode: str = "reporting_chart", timeout_seconds: int = 7) -> str:
        """
        Execute code, routing to sandbox or local based on configuration and circuit state.
        Returns output string compatible with REPLManager.run() format.
        """
        if not self.is_sandbox_mode:
            return replManager.run(code)

        if not self._circuit.allow_request:
            logger.warning("Circuit breaker OPEN: falling back to local execution")
            return replManager.run(code)

        try:
            result = self._call_sandbox(code, project_id, mode, timeout_seconds)
            self._circuit.record_success()
            return result
        except _SandboxUnavailableError as e:
            self._circuit.record_failure()
            logger.warning(f"Sandbox unavailable ({e}), falling back to local execution")
            return replManager.run(code)

    def run_batch(
        self,
        executions: list[dict],
        project_id: str,
        mode: str = "dashboard_filter",
    ) -> list[str]:
        """
        Execute a batch of code snippets.
        Each item in executions should have: {"code": str, "timeout_seconds": int}
        Returns list of output strings in the same order.
        """
        if not self.is_sandbox_mode:
            return [replManager.run(item["code"]) for item in executions]

        if not self._circuit.allow_request:
            logger.warning("Circuit breaker OPEN: falling back to local batch execution")
            return [replManager.run(item["code"]) for item in executions]

        try:
            results = self._call_sandbox_batch(executions, project_id, mode)
            self._circuit.record_success()
            return results
        except _SandboxUnavailableError as e:
            self._circuit.record_failure()
            logger.warning(f"Sandbox unavailable ({e}), falling back to local batch execution")
            return [replManager.run(item["code"]) for item in executions]

    def _call_sandbox(self, code: str, project_id: str, mode: str, timeout_seconds: int) -> str:
        """Make a signed HTTP call to the sandbox /v1/executions/sync endpoint."""
        execution_id = str(uuid.uuid4())

        payload = {
            "executionId": execution_id,
            "projectId": project_id,
            "userId": "",
            "mode": mode,
            "language": "python",
            "code": code,
            "timeoutSeconds": timeout_seconds,
            "maxOutputBytes": 262144,
            "context": {"allowedHelpers": ["fetch_data", "serializer"]},
            "metadata": {"source": "sandboxClient", "requestPath": ""},
        }

        body = json.dumps(payload).encode()
        headers = self._sign_request(execution_id, body)

        http_timeout = timeout_seconds + self._timeout_buffer + 5

        try:
            with httpx.Client(timeout=http_timeout) as client:
                response = client.post(
                    f"{self._sandbox_url}/v1/executions/sync",
                    content=body,
                    headers=headers,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            raise _SandboxUnavailableError(f"Connection failed: {e}")

        if response.status_code >= 500:
            raise _SandboxUnavailableError(f"HTTP {response.status_code}")

        result = response.json()
        return self._map_result_to_output(result)

    def _call_sandbox_batch(self, executions: list[dict], project_id: str, mode: str) -> list[str]:
        """Make a signed HTTP call to the sandbox /v1/executions/batch-sync endpoint."""
        batch_id = str(uuid.uuid4())

        batch_items = []
        max_timeout = 7
        for item in executions:
            exec_id = str(uuid.uuid4())
            timeout = item.get("timeout_seconds", 7)
            max_timeout = max(max_timeout, timeout)
            batch_items.append({
                "executionId": exec_id,
                "code": item["code"],
                "timeoutSeconds": timeout,
                "maxOutputBytes": 262144,
            })

        payload = {
            "batchId": batch_id,
            "projectId": project_id,
            "userId": "",
            "mode": mode,
            "executions": batch_items,
            "context": {"allowedHelpers": ["fetch_data", "serializer"]},
            "metadata": {"source": "sandboxClient", "requestPath": ""},
        }

        body = json.dumps(payload).encode()
        headers = self._sign_request(batch_id, body)

        http_timeout = max_timeout + self._timeout_buffer + 10

        try:
            with httpx.Client(timeout=http_timeout) as client:
                response = client.post(
                    f"{self._sandbox_url}/v1/executions/batch-sync",
                    content=body,
                    headers=headers,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            raise _SandboxUnavailableError(f"Connection failed: {e}")

        if response.status_code >= 500:
            raise _SandboxUnavailableError(f"HTTP {response.status_code}")

        result = response.json()
        results_list = result.get("results", [])

        output_strings = []
        for r in results_list:
            output_strings.append(self._map_result_to_output(r))

        return output_strings

    def _sign_request(self, execution_id: str, body: bytes) -> dict:
        """Generate HMAC signature headers."""
        timestamp = str(int(time.time()))
        message = f"{timestamp}.{execution_id}.".encode() + body
        signature = hmac.HMAC(
            self._shared_secret.encode(),
            message,
            hashlib.sha256,
        ).hexdigest()

        return {
            "Content-Type": "application/json",
            "X-Nubrix-Execution-Id": execution_id,
            "X-Nubrix-Timestamp": timestamp,
            "X-Nubrix-Signature": signature,
        }

    @staticmethod
    def _map_result_to_output(result: dict) -> str:
        """
        Map sandbox response back to REPLManager.run() compatible output.
        REPLManager returns stdout if present, else stderr.
        """
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        status = result.get("status", "")

        if status == "succeeded" and stdout:
            return stdout
        elif status == "timeout":
            return stderr or f"Execution timed out after {result.get('durationMs', 0) // 1000} seconds."
        elif status == "memory_limit":
            return stderr or "Execution exceeded memory limit."
        elif status == "rejected":
            return result.get("message", "Execution capacity exhausted. Try again later.")
        elif stdout:
            return stdout
        elif stderr:
            return stderr
        else:
            return stdout


class _SandboxUnavailableError(Exception):
    """Raised when the sandbox service is unreachable or returns 5xx."""
    pass


sandbox_client = SandboxClient()
