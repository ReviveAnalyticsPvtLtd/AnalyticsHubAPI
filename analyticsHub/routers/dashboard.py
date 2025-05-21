from ..models.requestModels import CreatePage, ExportToDashboard
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from supabase import create_client
from urllib.request import urlopen
from typing import Annotated
import uuid
import json
import os
import io

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.post("/createPage")
async def createPage(details: CreatePage, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            pageId = str(uuid.uuid4())
            if "dashboardConfig.json" in [x.get("name") for x in client.storage.from_("AnalyticsHub").list(path = details.projectId)]:
                fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "")
                dashboardConfig = json.loads(urlopen(fileUrl).read())
                dashboardConfig[pageId] = {"name": details.pageName, "widgets": []}
            else:
                dashboardConfig = {pageId: {"name": details.pageName, "widgets": []}}
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(dashboardConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/dashboardConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})            
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageId": pageId})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.get("/getAllPages")
async def getAllPages(projectId: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if "dashboardConfig.json" in [x.get("name") for x in client.storage.from_("AnalyticsHub").list(path = projectId)]:
                fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "dashboardConfig.json").replace(".parquet", "")
                dashboardConfig = json.loads(urlopen(fileUrl).read())
                pages = [dashboardConfig[x]["name"] for x in dashboardConfig.keys()]
            else:
                pages = list()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "pages": pages})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.post("/exportToDashboard")
async def exportToDashboard(details: ExportToDashboard, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "")
            dashboardConfig = json.loads(urlopen(fileUrl).read())
            pageDict = dashboardConfig.get(details.page)
            widgetId = str(uuid.uuid4())
            newWidget = {
                "id": widgetId,
                "chartType": details.chartType,
                "title": details.title,
                "xLabels": details.xLabels,
                "yLabels": details.yLabels,
                "data": {
                    "labels": details.labels,
                    "datasets": details.datasets
                },
                "layout": details.layout
            }
            pageDict["widgets"].append(newWidget)
            dashboardConfig[details.page] = pageDict
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(dashboardConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/dashboardConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})       
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "widgetId": widgetId})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.get("/getData")
async def getData(projectId: str, page: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "dashboardConfig.json").replace(".parquet", "")
            dashboardConfig = json.loads(urlopen(fileUrl).read())
            pageInfo = dashboardConfig.get(page)
            pageInfo["id"] = page
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageData": pageInfo})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
