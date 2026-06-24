"""
main.py

Sandbox Execution Service - FastAPI entrypoint.
Private internal service for executing LLM-generated Python code in isolated child processes.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["app"]

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.concurrency import init_pool
from app.core.logging import logger
from app.routes.health import router as health_router
from app.routes.executions import router as executions_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        f"Sandbox starting: max_concurrent={settings.max_concurrent_executions}, "
        f"max_queue={settings.max_queue_depth}, "
        f"memory_limit={settings.memory_limit_mb}MB"
    )
    init_pool(settings.max_concurrent_executions, settings.max_queue_depth)
    yield
    logger.info("Sandbox shutting down")


app = FastAPI(
    title="Sandbox Execution Service",
    description="Private internal service for isolated Python code execution",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal sandbox error", "detail": str(type(exc).__name__)},
    )


app.include_router(health_router)
app.include_router(executions_router)
