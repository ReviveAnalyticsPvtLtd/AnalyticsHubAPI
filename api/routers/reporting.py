"""
API router for reporting operations.

This module defines endpoints for generating charts and panel charts as part of the reporting functionality. It provides routes for creating visual representations of data based on user-supplied parameters.

Endpoints:
    - POST /generateChart: Generate a single chart from provided chart details.
    - POST /generatePanelChart: Generate a panel (multi-chart) visualization from provided panel chart details.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.models import GenerateChartInput, PanelChartDetails, GenerateChartsInParallel
from api.services.reportingService import reportingService
from fastapi.exceptions import HTTPException
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
from api.commons import verifyToken

router = APIRouter()
"""
Router for reporting-related endpoints.
"""

@router.post("/generateChart")
async def generateChart(chartDetails: GenerateChartInput, token = Depends(verifyToken)):
    """
    Generate a chart based on the provided chart details.

    Args:
        chartDetails (GenerateChartInput): The details required to generate the chart, including data, chart type, and configuration.
        token: Authorization token dependency, automatically injected by FastAPI for authentication.

    Returns:
        ORJSONResponse: A response containing the generated chart data in JSON format, or an error message if generation fails.

    Raises:
        HTTPException: If an error occurs during chart generation, returns a 500 status code with the error details.
    """
    try:
        response = reportingService.generateChart(chartDetails = chartDetails)
        return ORJSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/generatePanelChart")
async def generatePanelChart(panelChartDetails: PanelChartDetails, token = Depends(verifyToken)):
    """
    Generate a panel chart (multiple charts in a single view) based on the provided panel chart details.

    Args:
        panelChartDetails (PanelChartDetails): The details required to generate the panel chart, including data sources, layout, and configuration for each sub-chart.
        token: Authorization token dependency, automatically injected by FastAPI for authentication.

    Returns:
        ORJSONResponse: A response containing the generated panel chart data in JSON format, or an error message if generation fails.

    Raises:
        HTTPException: If an error occurs during panel chart generation, returns a 500 status code with the error details.
    """
    try:
        response = reportingService.generatePanelChart(panelChartDetails = panelChartDetails)
        return ORJSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/generateAndExportChartsInParallel")
async def generateAndExportChartsInParallel(details: GenerateChartsInParallel, token = Depends(verifyToken)):
    """
    Generate and export multiple charts in parallel based on the provided details.

    Args:
        details (GenerateChartsInParallel): The details for generating charts in parallel, including project ID and input queries.
        token: Authorization token dependency, automatically injected by FastAPI for authentication.

    Returns:
        ORJSONResponse: A response containing the generated chart data in JSON format, or an error message if generation fails.

    Raises:
        HTTPException: If an error occurs during chart generation, returns a 500 status code with the error details.
    """
    try:
        response = reportingService.generateChartsInParallel(details = details)
        return ORJSONResponse(status_code = 200, content = {"message": "Charts generated successfully", "pageData": response})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))