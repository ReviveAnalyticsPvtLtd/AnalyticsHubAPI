"""
auth.py

HMAC signature verification and replay protection for sandbox internal API.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["verify_request"]

import hashlib
import hmac
import time
from collections import OrderedDict
from threading import Lock

from fastapi import Request, HTTPException

from app.core.config import settings


class _ReplayGuard:
    """In-memory TTL set for execution ID deduplication."""

    def __init__(self, ttl_seconds: int, max_size: int = 10000):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: OrderedDict[str, float] = OrderedDict()
        self._lock = Lock()

    def check_and_add(self, execution_id: str) -> bool:
        """Returns True if this execution_id is a duplicate (already seen within TTL)."""
        now = time.time()
        with self._lock:
            self._evict(now)
            if execution_id in self._store:
                return True
            self._store[execution_id] = now
            return False

    def _evict(self, now: float):
        while self._store:
            oldest_key, oldest_time = next(iter(self._store.items()))
            if now - oldest_time > self._ttl:
                self._store.pop(oldest_key)
            else:
                break
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)


_replay_guard = _ReplayGuard(ttl_seconds=settings.replay_window_seconds)


def _compute_signature(timestamp: str, execution_id: str, body: bytes) -> str:
    message = f"{timestamp}.{execution_id}.".encode() + body
    return hmac.HMAC(
        settings.sandbox_shared_secret.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()


async def verify_request(request: Request) -> bytes:
    """
    Verify HMAC signature, timestamp freshness, and replay protection.
    Returns the raw request body on success.
    Raises HTTPException 401 on failure.
    """
    execution_id = request.headers.get("X-Nubrix-Execution-Id", "")
    timestamp_str = request.headers.get("X-Nubrix-Timestamp", "")
    signature = request.headers.get("X-Nubrix-Signature", "")

    if not all([execution_id, timestamp_str, signature]):
        raise HTTPException(status_code=401, detail="Missing authentication headers")

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp")

    now = int(time.time())
    if abs(now - timestamp) > settings.replay_window_seconds:
        raise HTTPException(status_code=401, detail="Request timestamp outside replay window")

    body = await request.body()

    expected_signature = _compute_signature(timestamp_str, execution_id, body)
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if _replay_guard.check_and_add(execution_id):
        raise HTTPException(status_code=409, detail="Duplicate execution ID")

    return body
