"""
API router for data blending operations.

This module provides endpoints for creating data blends, retrieving data sources, and fetching fields from sources.
"""
__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from utils.exceptionHandler import CustomException, raiseHttpException
from api.services.blendService import blendService
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
import asyncio
from api.commons import verifyToken, verifyProjectOwnership, verifyProjectOwnershipDirect, verifyUser, UserContext
from api.models import (
    GetFieldsFromSources,
    CreateDataBlend
)

router = APIRouter()
"""
Router for blend-related endpoints.
"""
    
@router.post("/createDataBlend")
async def createDataBlend(blendDetails: CreateDataBlend, user: UserContext = Depends(verifyUser)):
    """
    Create a new data blend using the provided blend details.

    Args:
        blendDetails (CreateDataBlend): The details required to create a data blend.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        await verifyProjectOwnershipDirect(blendDetails.projectId, user.userId)
        await asyncio.to_thread(blendService.createDataBlend, blendDetails = blendDetails)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Blend created successfully."})
    except CustomException as e:
        raiseHttpException(e)

@router.get("/getDataSources")
async def getDataSources(projectId: str, userId: str = Depends(verifyProjectOwnership)):
    """
    Retrieve available data sources for a given project.

    Args:
        projectId (str): The ID of the project to fetch data sources for.
        userId: Verified user identifier.

    Returns:
        ORJSONResponse: List of data sources or error message.
    """
    try:
        dataSources = await asyncio.to_thread(blendService.getDataSources, projectId = projectId)
        return ORJSONResponse(status_code = 200, content = dataSources)
    except CustomException as e:
        raiseHttpException(e)

@router.post("/getFieldsFromSources")
async def getFieldsFromSources(details: GetFieldsFromSources, user: UserContext = Depends(verifyUser)):
    """
    Get fields from the specified data sources.

    Args:
        details (GetFieldsFromSources): The details specifying which sources to fetch fields from.
        user: UserContext dependency.

    Returns:
        ORJSONResponse: List of fields or error message.
    """
    try:
        await verifyProjectOwnershipDirect(details.projectId, user.userId)
        response = await asyncio.to_thread(blendService.getFieldsFromSources, details = details)
        return ORJSONResponse(status_code = 200, content = response)
    except CustomException as e:
        raiseHttpException(e)