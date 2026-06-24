"""
health.py

Health check endpoints for sandbox service.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["router"]

import os
import tempfile

import redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.concurrency import execution_pool

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness():
    """Returns 200 if the FastAPI process is alive."""
    return {"status": "alive"}


@router.get("/ready")
async def readiness():
    """
    Returns 200 only when:
    - Redis is reachable
    - Active execution count is below hard limit
    - Temp directory is writable
    """
    checks = {}

    try:
        r = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            socket_timeout=3,
        )
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"fail: {e}"
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})

    if execution_pool:
        pool_stats = execution_pool.stats
        checks["execution_pool"] = pool_stats
        if pool_stats["active"] >= pool_stats["max_concurrent"] and pool_stats["waiting"] >= pool_stats["max_queue_depth"]:
            checks["capacity"] = "exhausted"
            return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
        checks["capacity"] = "ok"
    else:
        checks["execution_pool"] = "not_initialized"
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})

    try:
        tmp_dir = tempfile.gettempdir()
        test_file = os.path.join(tmp_dir, ".sandbox_health_check")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        checks["temp_dir"] = "ok"
    except Exception as e:
        checks["temp_dir"] = f"fail: {e}"
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})

    return {"status": "ready", "checks": checks}
