from ..models.requestModels import GenerateChartInput, GetFieldDetailsForChart
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from urllib.request import urlopen
from typing import Annotated
from . import pipeline
import json
import os

router = APIRouter()
security = HTTPBearer()

@router.post("/generateChart")
async def generateChart(chartDetails: GenerateChartInput, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = chartDetails.projectId, fileName = "metadata.json").replace(".parquet", "")
            response = pipeline.generateChart(
                inputQuery = chartDetails.inputQuery,
                projectId = chartDetails.projectId,
                metadata = json.loads(urlopen(fileUrl).read())
            )
            return JSONResponse(status_code = 200, content = response)
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/getFieldDetailsForChart")
async def generateChart(inputDetails: GetFieldDetailsForChart, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = inputDetails.projectId, fileName = "metadata.json").replace(".parquet", "")
            metadataJson = json.loads(urlopen(fileUrl).read())
            allColumns = list()
            numericals = list()
            categoricals = list()
            for table in metadataJson.keys():
                for column in metadataJson[table]["columns"]:
                    allColumns.append(column)
            for column in allColumns:
                if (column["type"] == "object") or (column["type"] == "bool"):
                    categoricals.append(column["name"])
                else:
                    numericals.append(column["name"])
            if inputDetails.chartType == "bar":
                response = {
                    "xField": categoricals,
                    "yField": numericals 
                }
            elif inputDetails.chartType == "scatter":
                response = {
                    "xField": numericals,
                    "yField": numericals 
                }
            elif inputDetails.chartType == "line":
                response = {
                    "xField": categoricals,
                    "yField": numericals 
                }
            else:
                response = {}
            return JSONResponse(status_code = 200, content = response)
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")