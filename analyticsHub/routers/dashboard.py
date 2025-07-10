from ..models.requestModels import CreatePage, ExportToDashboard, EditWidgetPosition, GetData, DeleteDashboardElement
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from ..components import replManager
from supabase import create_client
from urllib.request import urlopen
from typing import Annotated
import uuid
import json
import time
import os
import io
import re

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
                fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
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
                fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "dashboardConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                dashboardConfig = json.loads(urlopen(fileUrl).read())
                pages = [{"pageName": dashboardConfig[x]["name"], "pageId": x} for x in dashboardConfig.keys()]
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
            fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            dashboardConfig = json.loads(urlopen(fileUrl).read())
            pageDict = dashboardConfig.get(details.page)
            widgetId = str(uuid.uuid4())
            newWidget = {
                "id": widgetId,
                "chartType": details.chartType,
                "title": details.title,
                "label": details.label,
                "xLabels": details.xLabels,
                "yLabels": details.yLabels,
                "data": details.data,
                "layout": details.layout,
                "generatedCode": details.generatedCode
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
    
@router.post("/getData")
async def getData(details: GetData, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            dashboardConfig = json.loads(urlopen(fileUrl).read())
            pageInfo = dashboardConfig.get(details.page)
            pageInfo["id"] = details.page
            if not details.filters:
                for widget in pageInfo["widgets"]: widget.pop("generatedCode")
            else:
                widgets = pageInfo.get("widgets")
                codes = {x.get("id"): x.get("generatedCode") for x in widgets}
                keys = codes.keys()
                for key in keys:
                    if "```" in codes.get(key):
                        newCode = "\n".join(codes.get(key).split("```")[-2].split("\n")[1:])
                        newCode = re.sub(r'fetch_data\(([^)]+)\)', r'fetch_data(\1, {filters})'.format(filters = details.filters), newCode)
                        for widget in widgets:
                            if widget.get("id") == key:
                                widget.pop("generatedCode")
                                result = replManager.run(newCode)
                                try:
                                    resultDict = json.loads(result)
                                    widget.update(resultDict)
                                except:
                                    widgetChartType = widget.get("chartType")
                                    if widgetChartType == "card":
                                        widget["data"] = None
                                    else:
                                        dataKey = widget.get("data")
                                        datasets = dataKey.get("datasets")
                                        for dataset in datasets:
                                            dataset["data"] = list()
                            else:
                                continue
                    else:
                        newCode = re.sub(r'fetch_data\(([^)]+)\)', r'fetch_data(\1, {filters})'.format(filters = details.filters), codes[key])
                        for widget in widgets:
                            if widget.get("id") == key:
                                widget.pop("generatedCode")
                                result = replManager.run(newCode)
                                try:
                                    resultDict = json.loads(result)
                                    widget.update(resultDict)
                                except:
                                    widgetChartType = widget.get("chartType")
                                    if widgetChartType == "card":
                                        widget["data"] = None
                                    else:
                                        dataKey = widget.get("data")
                                        datasets = dataKey.get("datasets")
                                        for dataset in datasets:
                                            dataset["data"] = list()
                            else:
                                continue
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageData": pageInfo})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.post("/editWidgetPosition")
async def editWidgetPosition(details: EditWidgetPosition, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            dashboardConfig = json.loads(urlopen(fileUrl).read())
            pageInfo = dashboardConfig.get(details.pageId)
            widgets = pageInfo.get("widgets")
            for newWidget in details.widgets:
                newWidgetId = newWidget.get("id")
                for widget in widgets:
                    widgetId = widget.get("id")
                    if widgetId == newWidgetId:
                        widget["layout"] = newWidget["layout"]
                    else:
                        continue
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(dashboardConfig, indent = 4).encode("utf-8"))
                buffer.seek(0)
                client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/dashboardConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageData": pageInfo})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.delete("/deleteDashboardElement")
async def deleteDashboardElement(details: DeleteDashboardElement, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "dashboardConfig.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            dashboardConfig = json.loads(urlopen(fileUrl).read())
            if details.deletionObject == "page":
                dashboardConfig.pop(details.id)
            elif details.deletionObject == "widget":
                for pageId in dashboardConfig.keys():
                    page = dashboardConfig.get(pageId)
                    pageWidgets = page.get("widgets")
                    for widget in pageWidgets:
                        if widget.get("id") == details.id:
                            pageWidgets.remove(widget)
                            break
                        else: continue
                    break
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(dashboardConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = client.storage.from_("AnalyticsHub").upload(path = f"{details.projectId}/dashboardConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})  
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "element deleted successfully."})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")