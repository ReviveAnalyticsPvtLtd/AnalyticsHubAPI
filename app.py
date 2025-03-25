from analyticsHub.routers import authentication, projectManager, dataLoader, reportingTool, utilities
from langchain_experimental.utilities import PythonREPL
from fastapi.middleware.cors import CORSMiddleware
from analyticsHub.utils.functions import readYaml
from analyticsHub.components import replManager
from supabase import create_client
from fastapi import FastAPI
import uvicorn
import os

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

app.include_router(authentication.router, prefix = "/auth", tags = ["Authentication"])
app.include_router(projectManager.router, prefix = "/projects", tags = ["Project Management"])
app.include_router(dataLoader.router, prefix = "/loaders", tags = ["Data Loader"])
app.include_router(reportingTool.router, prefix = "/reportingTool", tags = ["Reporting Tool"])
app.include_router(utilities.router, prefix = "/utils", tags = ["Utilities"])

@app.on_event("startup")
async def startupEvent():
    projectIds = [x["projectId"] for x in client.table("Projects").select("projectId").execute().data]
    for id in projectIds:
        replManager.manager[id] = PythonREPL()
        _ = replManager.manager[id].run(readYaml("params.yaml")["redisFunctionCode"])
        _ = replManager.manager[id].run(readYaml("params.yaml")["jsonSerializer"])
        _ = replManager.manager[id].run(("globals()['__name__'] = '__main__'"))
        _ = replManager.manager[id].run("globals().update(locals())")

if __name__ == "__main__":
    uvicorn.run("app:app", host = "0.0.0.0", port = 8000, reload = True)