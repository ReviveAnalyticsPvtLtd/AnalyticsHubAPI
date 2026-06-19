"""
Main entry point for the NubrixAI FastAPI application.

This module initializes the FastAPI app, configures middleware, and includes all API routers for authentication, project management, data loading, blends, reporting, dashboard, and utilities.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"


import ssl
ssl._create_default_https_context = ssl._create_unverified_context

from api.routers import authentication, manager, dataLoader, reporting, utils, blends, dashboard, subscriptions, webhooks, billingAdmin, transformations
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from utils.logger import logger
from fastapi.responses import ORJSONResponse
from fastapi import FastAPI, Request, HTTPException
import psutil
import os

app = FastAPI(
    title = "NubrixAI",
    summary = "API Endpoints for NubrixAI.",
    version = "1.0",
    root_path = "/api/latest",
    docs_url = "/documentation/docs",
    redoc_url = "/documentation/redoc"
)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Global exception handler for HTTPException.
    
    If the detail is a dict containing 'status' and 'message', it returns a 
    flat response. Otherwise, it returns the standard detail format.
    """
    if isinstance(exc.detail, dict) and "status" in exc.detail and "message" in exc.detail:
        return ORJSONResponse(
            status_code=exc.status_code,
            content=exc.detail
        )
    return ORJSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"],
)
app.add_middleware(
    GZipMiddleware, 
    minimum_size=1000, 
    compresslevel=5
)

@app.on_event("startup")
async def stats():
    """
    FastAPI startup event handler.

    Logs system memory and CPU usage statistics at application startup.
    """
    memory = psutil.virtual_memory()
    cpu_usage = psutil.cpu_percent(interval=1, percpu=True)
    totalUsage = psutil.cpu_percent(interval=1)
    logger.debug(f"RAM Usage Percentage: {memory.percent}%")
    logger.debug(f"Total CPU Utilization: {totalUsage}%")
    logger.debug(f"Total CPU Usage Per Core: {cpu_usage}")

app.include_router(authentication.router, prefix = "/auth", tags = ["Authentication"])
app.include_router(manager.router, prefix = "/projects", tags = ["Project Management"])
app.include_router(dataLoader.router, prefix = "/loaders", tags = ["Data Loader"])
app.include_router(blends.router, prefix = "/blends", tags = ["Blends"])
app.include_router(reporting.router, prefix = "/reportingTool", tags = ["Reporting Tool"])
app.include_router(dashboard.router, prefix = "/dashboard", tags = ["Dashboard"])
app.include_router(utils.router, prefix = "/utils", tags = ["Utilities"])
app.include_router(subscriptions.router, prefix = "/subscriptions", tags = ["Subscriptions"])
app.include_router(webhooks.router, prefix = "/webhooks", tags = ["Webhooks"])
app.include_router(billingAdmin.router, prefix = "/billing-admin", tags = ["Billing Admin"])
app.include_router(transformations.router, prefix = "/transformations", tags = ["Transformations"])
