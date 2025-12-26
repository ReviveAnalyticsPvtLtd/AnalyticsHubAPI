"""
API router for data subscription operations.

This module provides endpoints for creating data blends, retrieving data sources, and fetching fields from sources.
"""
__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.services.subscriptionService import subscriptionService
from fastapi.exceptions import HTTPException
from fastapi.responses import ORJSONResponse
from fastapi import APIRouter, Depends
from api.commons import verifyToken

router = APIRouter()
"""
Router for subscription-related endpoints.
"""
    
@router.get("/activateFreeTrial/{userId}")
async def activateFreeTrial(userId: str, token = Depends(verifyToken)):
    """
    Activate a free trial for a user.

    Args:
        userId (str): The ID of the user to activate the free trial for.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Success or error message.
    """
    try:
        subscriptionService.activateFreeTrial(userId = userId)
        return ORJSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Free trial activated successfully."})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = str(e))