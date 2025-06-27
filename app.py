from analyticsHub.routers import authentication, projectManager, dataLoader, reportingTool, utilities, blends, dashboard
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from analyticsHub.utils.functions import getConfig
from api_analytics.fastapi import Analytics
from supabase import create_client
from fastapi import FastAPI
import psutil
import os

config = getConfig(os.path.join(os.getcwd(), "config.ini"))
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

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
    memory = psutil.virtual_memory()
    cpu_usage = psutil.cpu_percent(interval=1, percpu=True)
    totalUsage = psutil.cpu_percent(interval=1)
    print(f"RAM Usage Percentage: {memory.percent}%")
    print(f"Total CPU Utilization: {totalUsage}%")
    print(f"Total CPU Usage Per Core: {cpu_usage}")

app.include_router(authentication.router, prefix = "/auth", tags = ["Authentication"])
app.include_router(projectManager.router, prefix = "/projects", tags = ["Project Management"])
app.include_router(dataLoader.router, prefix = "/loaders", tags = ["Data Loader"])
app.include_router(blends.router, prefix = "/blends", tags = ["Blends"])
app.include_router(reportingTool.router, prefix = "/reportingTool", tags = ["Reporting Tool"])
app.include_router(dashboard.router, prefix = "/dashboard", tags = ["Dashboard"])
app.include_router(utilities.router, prefix = "/utils", tags = ["Utilities"])