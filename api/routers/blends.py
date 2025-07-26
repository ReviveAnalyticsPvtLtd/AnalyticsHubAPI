"""
API router for data blending operations.

This module provides endpoints for creating data blends, retrieving data sources, and fetching fields from sources.
"""
__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.services.blendService import blendService
from fastapi.exceptions import HTTPException
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
from api.commons import verifyToken
from api.models import (
    GetFieldsFromSources,
    CreateDataBlend
)

router = APIRouter()
"""
Router for blend-related endpoints.
"""
    
@router.post("/createDataBlend")
async def createDataBlend(blendDetails: CreateDataBlend, token = Depends(verifyToken)):
    """
    Create a new data blend using the provided blend details.

    Args:
        blendDetails (CreateDataBlend): The details required to create a data blend.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        blendService.createDataBlend(blendDetails = blendDetails)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Blend created successfully."})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = e)

@router.get("/getDataSources")
async def getDataSources(projectId: str, token = Depends(verifyToken)):
    """
    Retrieve available data sources for a given project.

    Args:
        projectId (str): The ID of the project to fetch data sources for.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of data sources or error message.
    """
    try:
        dataSources = blendService.getDataSources(projectId = projectId)
        return ORJSONResponse(status_code = 200, content = dataSources)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = e)

@router.post("/getFieldsFromSources")
async def getFieldsFromSources(details: GetFieldsFromSources, token = Depends(verifyToken)):
    """
    Get fields from the specified data sources.

    Args:
        details (GetFieldsFromSources): The details specifying which sources to fetch fields from.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of fields or error message.
    """
    try:
        response = blendService.getFieldsFromSources(details = details)
        return ORJSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = e)
