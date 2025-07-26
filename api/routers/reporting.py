"""
API router for reporting operations.

This module provides endpoints for generating charts and panel charts for reports.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from ..models import GenerateChartInput, PanelChartDetails
from ..services.reportingService import reportingService
from fastapi.exceptions import HTTPException
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
from ..commons import verifyToken

router = APIRouter()
"""
Router for reporting-related endpoints.
"""

@router.post("/generateChart")
async def generateChart(chartDetails: GenerateChartInput, token = Depends(verifyToken)):
    """
    Generate a chart based on the provided chart details.

    Args:
        chartDetails (GenerateChartInput): The details required to generate the chart.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Chart data or error message.
    """
    try:
        response = reportingService.generateChart(chartDetails = chartDetails)
        return ORJSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = e)
    
@router.post("/generatePanelChart")
async def generatePanelChart(panelChartDetails: PanelChartDetails, token = Depends(verifyToken)):
    """
    Generate a panel chart based on the provided panel chart details.

    Args:
        panelChartDetails (PanelChartDetails): The details required to generate the panel chart.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Panel chart data or error message.
    """
    try:
        response = reportingService.generatePanelChart(panelChartDetails = panelChartDetails)
        return ORJSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = e)