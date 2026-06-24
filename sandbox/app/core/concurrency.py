"""
concurrency.py

Semaphore-based execution pool with bounded queue depth.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["execution_pool", "ExecutionPool", "CapacityExhaustedError"]

import asyncio


class CapacityExhaustedError(Exception):
    """Raised when both active slots and queue depth are at capacity."""
    pass


class ExecutionPool:
    """
    Manages bounded concurrency using an asyncio.Semaphore.
    Tracks active and waiting counts for health reporting.
    """

    def __init__(self, max_concurrent: int, max_queue_depth: int):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._max_queue_depth = max_queue_depth
        self._waiting = 0
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            if self._waiting >= self._max_queue_depth:
                raise CapacityExhaustedError()
            self._waiting += 1

        try:
            await self._semaphore.acquire()
        except BaseException:
            async with self._lock:
                self._waiting -= 1
            raise

        async with self._lock:
            self._waiting -= 1
            self._active += 1

    async def release(self):
        async with self._lock:
            self._active -= 1
        self._semaphore.release()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    @property
    def stats(self) -> dict:
        return {
            "active": self._active,
            "waiting": self._waiting,
            "max_concurrent": self._max_concurrent,
            "max_queue_depth": self._max_queue_depth,
        }


execution_pool: ExecutionPool | None = None


def init_pool(max_concurrent: int, max_queue_depth: int) -> ExecutionPool:
    """Initialize the global execution pool. Call once at app startup."""
    global execution_pool
    execution_pool = ExecutionPool(max_concurrent, max_queue_depth)
    return execution_pool
