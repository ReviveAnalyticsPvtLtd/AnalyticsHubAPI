from analyticsHub.routers import authentication, projectManager
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
import uvicorn

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

app.include_router(authentication.router, prefix = "/auth", tags = ["Authentication"])
app.include_router(projectManager.router, prefix = "/projects", tags = ["Project Management"])


if __name__ == "__main__":
    uvicorn.run("app:app", host = "0.0.0.0", port = 8000, reload = False)