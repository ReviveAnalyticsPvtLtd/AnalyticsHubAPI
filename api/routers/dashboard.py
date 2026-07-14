"""
API router for dashboard operations.

This module provides endpoints for creating pages, exporting widgets, retrieving data, editing widget positions, deleting dashboard elements, and fetching columns for dashboards.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from utils.exceptionHandler import CustomException, raiseHttpException
from api.services.dashboardService import dashboardService
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
import asyncio
from api.commons import verifyToken, verifyProjectOwnership, verifyProjectOwnershipDirect, verifyUser, UserContext
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
async def createPage(details: CreatePage, user: UserContext = Depends(verifyUser)):
    """
    Create a new dashboard page.

    Args:
        details (CreatePage): The details required to create a new page.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success message with the new page ID or error message.
    """
    try:
        await verifyProjectOwnershipDirect(details.projectId, user.userId)
        pageId = await asyncio.to_thread(dashboardService.createPage, details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageId": pageId})
    except CustomException as e:
        raiseHttpException(e)
    
@router.get("/getAllPages")
async def getAllPages(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Retrieve all pages for a given project.

    Args:
        projectId (str): The ID of the project.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: List of pages or error message.
    """
    try:
        pages = await asyncio.to_thread(dashboardService.getAllPages, projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pages": pages})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/exportToDashboard")
async def exportToDashboard(details: ExportToDashboard, user: UserContext = Depends(verifyUser)):
    """
    Export a widget to the dashboard.

    Args:
        details (ExportToDashboard): The details required to export a widget.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success message with the widget ID or error message.
    """
    try:
        await verifyProjectOwnershipDirect(details.projectId, user.userId)
        widgetId = await asyncio.to_thread(dashboardService.exportToDashboard, details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "widgetId": widgetId})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/getData")
async def getData(details: GetData, user: UserContext = Depends(verifyUser)):
    """
    Retrieve data for a dashboard page.

    Args:
        details (GetData): The details specifying which data to retrieve.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Page data or error message.
    """
    try:
        await verifyProjectOwnershipDirect(details.projectId, user.userId)
        pageInfo = await asyncio.to_thread(dashboardService.getData, details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageData": pageInfo})
    except CustomException as e:
        raiseHttpException(e)
    
@router.put("/editWidgetPosition")
async def editWidgetPosition(details: EditWidgetPosition, user: UserContext = Depends(verifyUser)):
    """
    Edit the position of a widget on a dashboard page.

    Args:
        details (EditWidgetPosition): The details for editing widget position.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Updated page data or error message.
    """
    try:
        await verifyProjectOwnershipDirect(details.projectId, user.userId)
        pageInfo = await asyncio.to_thread(dashboardService.editWidgetPosition, details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "pageData": pageInfo})
    except CustomException as e:
        raiseHttpException(e)

@router.delete("/deleteDashboardElement")
async def deleteDashboardElement(details: DeleteDashboardElement, user: UserContext = Depends(verifyUser)):
    """
    Delete an element from the dashboard.

    Args:
        details (DeleteDashboardElement): The details of the element to delete.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(details.projectId, user.userId)
        await asyncio.to_thread(dashboardService.deleteDashboardElement, details = details)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "element deleted successfully."})
    except CustomException as e:
        raiseHttpException(e)

@router.get("/getAllColumns/{projectId}")
async def getAllColumns(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Retrieve all columns for a given project.

    Args:
        projectId (str): The ID of the project.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: Details of columns or error message.
    """
    try:
        results = await asyncio.to_thread(dashboardService.getAllColumns, projectId = projectId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "details": results})
    except CustomException as e:
        raiseHttpException(e)
    
@router.get("/dashboardRefresh/{projectId}")
async def dashboardRefresh(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    try:
        result = await asyncio.to_thread(dashboardService.pullDataInParallel, projectId = projectId)
        result.update({"message": "Data refreshed successfully."})
        return ORJSONResponse(status_code = 200, content = result)
    except CustomException as e:
        raiseHttpException(e)