"""
API router for dashboard operations.

This module provides endpoints for creating pages, exporting widgets, retrieving data, editing widget positions, deleting dashboard elements, and fetching columns for dashboards.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.services.dashboardService import dashboardService
from fastapi.exceptions import HTTPException
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
from api.commons import verifyToken
from api.models import (
    DeleteDashboardElement,
    ExportToDashboard,
    EditWidgetPosition,
    CreatePage,
    GetData
)

router = APIRouter()
"""
Router for dashboard-related endpoints.
"""

@router.post("/createPage")
async def createPage(details: CreatePage, token = Depends(verifyToken)):
    """
    Create a new dashboard page.

    Args:
        details (CreatePage): The details required to create a new page.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success message with the new page ID or error message.
    """
    try:
        pageId = dashboardService.createPage(details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageId": pageId})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.get("/getAllPages")
async def getAllPages(projectId: str, token = Depends(verifyToken)):
    """
    Retrieve all pages for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of pages or error message.
    """
    try:
        pages = dashboardService.getAllPages(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pages": pages})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/exportToDashboard")
async def exportToDashboard(details: ExportToDashboard, token = Depends(verifyToken)):
    """
    Export a widget to the dashboard.

    Args:
        details (ExportToDashboard): The details required to export a widget.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success message with the widget ID or error message.
    """
    try:
        widgetId = dashboardService.exportToDashboard(details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "widgetId": widgetId})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.post("/getData")
async def getData(details: GetData, token = Depends(verifyToken)):
    """
    Retrieve data for a dashboard page.

    Args:
        details (GetData): The details specifying which data to retrieve.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Page data or error message.
    """
    try:
        pageInfo = dashboardService.getData(details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageData": pageInfo})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))
    
@router.put("/editWidgetPosition")
async def editWidgetPosition(details: EditWidgetPosition, token = Depends(verifyToken)):
    """
    Edit the position of a widget on a dashboard page.

    Args:
        details (EditWidgetPosition): The details for editing widget position.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Updated page data or error message.
    """
    try:
        pageInfo = dashboardService.editWidgetPosition(details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageData": pageInfo})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))

@router.delete("/deleteDashboardElement")
async def deleteDashboardElement(details: DeleteDashboardElement, token = Depends(verifyToken)):
    """
    Delete an element from the dashboard.

    Args:
        details (DeleteDashboardElement): The details of the element to delete.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        dashboardService.deleteDashboardElement(details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "element deleted successfully."})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/getAllColumns/{projectId}")
async def getAllColumns(projectId: str, token = Depends(verifyToken)):
    """
    Retrieve all columns for a given project.

    Args:
        projectId (str): The ID of the project.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Details of columns or error message.
    """
    try:
        results = dashboardService.getAllColumns(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "details": results})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.get("/dashboardRefresh/{projectId}")
async def dashboardRefresh(projectId: str, token = Depends(verifyToken)):
    try:
        result = dashboardService.pullDataInParallel(projectId = projectId)
        result.update({"message": "Data refreshed successfully."})
        return ORJSONResponse(status_code = 200, content = result)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")