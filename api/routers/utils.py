"""
API router for utility operations.

This module provides endpoints for speech transcription, hybrid image-to-insights generation,
dashboard insight retrieval and lifecycle management, sending forecasts, temporary functions,
and websocket task status updates.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from utils.exceptionHandler import CustomException, raiseHttpException
from fastapi import status, APIRouter, Depends, WebSocket, WebSocketDisconnect
from api.models import SpeechToTextModel, ImageToInsightsModel, UpdateDashboardInsightStatus
from fastapi.security import HTTPAuthorizationCredentials
from api.services.utilityService import utilityService
from nubrix.triggers.celery import celeryApp
from fastapi.responses import ORJSONResponse
from api.commons import verifyToken
import asyncio

router = APIRouter()
"""
Router for utility-related endpoints.
"""

@router.post("/getSpeechTranscript")
async def getSpeechTranscript(speechToText: SpeechToTextModel, token = Depends(verifyToken)):
    """
    Get the transcript of a speech audio file.

    Args:
        speechToText (SpeechToTextModel): The speech-to-text model input.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Transcription text or error message.
    """
    try:
        transcriptText = utilityService.getSpeechTranscript(speechToText = speechToText)
        return ORJSONResponse(status_code = 200, content = {"transcriptionText": transcriptText})
    except CustomException as e:
        raiseHttpException(e)
    
@router.post("/getInsightsFromImage")
async def getInsightsFromImage(imageToInsights: ImageToInsightsModel, token = Depends(verifyToken)):
    """
    Extract structured, evidence-backed business insights from a base64-encoded
    dashboard image using the hybrid data + statistics + domain + LLM pipeline.

    Args:
        imageToInsights (ImageToInsightsModel): Input model containing the base64 image,
            project context, and user objectives.
        token: Authorization token dependency (for access control).

    Returns:
        ORJSONResponse: Structured JSON with diagnostic_insights, prescriptive_actions,
            and missing_data.
    """
    try:
        insights = utilityService.getInsightsFromImage(imageToInsights=imageToInsights)
        return ORJSONResponse(status_code=200, content={"insights": insights})
    except CustomException as e:
        raiseHttpException(e)

@router.get("/getDashboardInsights/{projectId}")
async def getDashboardInsights(projectId: str, token = Depends(verifyToken)):
    """
    Retrieve all persisted dashboard insights for a project.

    Args:
        projectId (str): The project identifier.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: List of insight records with status lifecycle.
    """
    try:
        records = utilityService.getDashboardInsights(projectId=projectId)
        return ORJSONResponse(status_code=200, content={"insights": records})
    except CustomException as e:
        raiseHttpException(e)

@router.patch("/updateDashboardInsightStatus")
async def updateDashboardInsightStatus(body: UpdateDashboardInsightStatus, token = Depends(verifyToken)):
    """
    Update the lifecycle status of a persisted dashboard insight.

    Args:
        body (UpdateDashboardInsightStatus): Model containing projectId, insightId, and new status.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: The updated insight record.
    """
    try:
        record = utilityService.updateDashboardInsightStatus(
            projectId=body.projectId,
            insightId=body.insightId,
            status=body.status,
        )
        return ORJSONResponse(status_code=200, content={"insight": record})
    except CustomException as e:
        raiseHttpException(e)

@router.get("/sendForecasts")
async def sendForecasts(token = Depends(verifyToken)):
    """
    Send forecasts using the utility service.

    Args:
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Task ID, trigger name, and task status or error message.
    """
    try:
        r = utilityService.sendForecasts()
        return ORJSONResponse(status_code = 200, content = {"taskId": r.task_id, "triggerName": "forecast", "taskStatus": r.status}) 
    except CustomException as e:
        raiseHttpException(e)

@router.get("/temp/{num}")
async def tempFunc(num: int, token = Depends(verifyToken)):
    """
    Temporary function for testing or utility purposes.

    Args:
        num (int): A number parameter for the temporary function.
        token: Authorization token dependency.

    Returns:
        ORJSONResponse: Result or error message.
    """
    try:
        result = utilityService.tempFunc(num = num)
        return ORJSONResponse(status_code = 200, content = result)
    except CustomException as e:
        raiseHttpException(e)
    
@router.websocket("/ws/getTaskStatus")
async def getTaskStatus(websocket: WebSocket):
    """
    WebSocket endpoint to get the status of a background task.

    Args:
        websocket (WebSocket): The WebSocket connection instance.

    Returns:
        None. Sends task status updates over the WebSocket connection.
    """
    await websocket.accept()
    token = websocket.query_params.get("token")
    credentials = HTTPAuthorizationCredentials(scheme = "Bearer", credentials = token)
    if not token or not verifyToken(token = credentials):
        await websocket.close(code = status.WS_1008_POLICY_VIOLATION)
        return 
    await websocket.send_json({"status": "RUNNING"})
    try:
        taskId = websocket.query_params.get("taskId")
        r = celeryApp.AsyncResult(taskId)
        while not r.ready():
            await asyncio.sleep(5)
            continue            
        await websocket.send_json({
            "taskId": r.task_id,
            "status": r.status,
            "taskResponseCode": r.get()
        })
        await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_json({"status": "ERROR", "errorDetail": str(e)})
        await websocket.close()
