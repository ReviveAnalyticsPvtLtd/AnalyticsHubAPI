"""
Main entry point for the AnalyticsHub FastAPI application.

This module initializes the FastAPI app, configures middleware, and includes all API routers for authentication, project management, data loading, blends, reporting, dashboard, and utilities.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"


from api.routers import authentication, manager, dataLoader, reporting, utils, blends, dashboard
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from api_analytics.fastapi import Analytics
from utils.logger import logger
from fastapi import FastAPI
import psutil
import os

app = FastAPI(
    title = "AnalyticsHub",
    summary = "API Endpoints for AnalyticsHub.",
    version = "1.0",
    root_path = "/api/latest",
    docs_url = "/documentation/docs",
    redoc_url = "/documentation/redoc"
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
app.add_middleware(
    Analytics,
    api_key = os.environ["FASTAPI_ANALYTICS_KEY"]
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