from ..models.requestModels import GenerateChartInput, PanelChartDetails
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
    
@router.post("/generatePanelChart")
async def generatePanelChart(panelChartDetails: PanelChartDetails, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            response = pipeline.generateChartFromPanel(
                projectId = panelChartDetails.projectId,
                chartType = panelChartDetails.chartType,
                xAxis = panelChartDetails.xAxis,
                yAxis = panelChartDetails.yAxis,
                aggregationMetric = panelChartDetails.aggregationMetric,
                dataSource = panelChartDetails.dataSource
            )
            return JSONResponse(status_code = 200, content = response)
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")