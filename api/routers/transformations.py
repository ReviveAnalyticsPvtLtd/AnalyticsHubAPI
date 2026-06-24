"""
API router for AI-powered data transformations.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["router"]


from api.models import (
    ApplyTransformationRequest,
    ApproveMessageRequest,
    CreateTransformationRequest,
    SendMessageRequest,
    RollbackRequest,
)
from api.services.transformationService import transformationService
from utils.exceptionHandler import CustomException, raiseHttpException
from fastapi.responses import ORJSONResponse, StreamingResponse
from fastapi import APIRouter, Depends
from api.commons import verifyToken


router = APIRouter()
"""
Router for transformation-related endpoints.
"""


@router.post("")
async def createTransformation(
    projectId: str,
    request: CreateTransformationRequest,
    token=Depends(verifyToken),
):
    """
    Create a new transformation workspace for a project.
    """
    try:
        result = await transformationService.createTransformation(
            projectId=projectId,
            name=request.transformation_name,
            description=request.description,
        )
        return ORJSONResponse(status_code=200, content=result)
    except CustomException as e:
        raiseHttpException(e)


@router.get("")
async def listTransformations(projectId: str, token=Depends(verifyToken)):
    """
    List all transformations for a project.
    """
    try:
        result = await transformationService.listTransformations(projectId=projectId)
        return ORJSONResponse(status_code=200, content={"data": result})
    except CustomException as e:
        raiseHttpException(e)


@router.get("/{transformation_id}/messages")
async def getTransformationMessages(
    transformation_id: str,
    projectId: str,
    token=Depends(verifyToken),
):
    """
    Return persisted chat history for a transformation.
    """
    try:
        result = await transformationService.getMessages(
            projectId=projectId,
            transformationId=transformation_id,
        )
        return ORJSONResponse(status_code=200, content={"messages": result})
    except CustomException as e:
        raiseHttpException(e)


@router.post("/{transformation_id}/messages")
async def sendTransformationMessage(
    transformation_id: str,
    projectId: str,
    request: SendMessageRequest,
    token=Depends(verifyToken),
):
    """
    Send a user message and stream the assistant response over SSE.
    """
    return StreamingResponse(
        transformationService.sendMessageStream(
            projectId=projectId,
            transformationId=transformation_id,
            content=request.content,
        ),
        media_type="text/event-stream",
    )


@router.post("/{transformation_id}/messages/{message_id}")
async def approveTransformationMessage(
    transformation_id: str,
    message_id: str,
    projectId: str,
    request: ApproveMessageRequest,
    token=Depends(verifyToken),
):
    """
    Approve a Mermaid artifact and return a transformed table preview.
    """
    try:
        result = await transformationService.approveMessage(
            projectId=projectId,
            transformationId=transformation_id,
            messageId=message_id,
            newTransformedTableName=request.newTransformedTableName,
        )
        return ORJSONResponse(status_code=200, content=result)
    except CustomException as e:
        raiseHttpException(e)


@router.post("/{transformation_id}/messages/{message_id}/apply")
async def applyTransformationMessage(
    transformation_id: str,
    message_id: str,
    projectId: str,
    request: ApplyTransformationRequest,
    token=Depends(verifyToken),
):
    """
    Persist an approved transformation output as a project table.
    """
    try:
        result = await transformationService.applyTransformation(
            projectId=projectId,
            transformationId=transformation_id,
            messageId=message_id,
            newTransformedTableName=request.newTransformedTableName,
        )
        return ORJSONResponse(status_code=200, content=result)
    except CustomException as e:
        raiseHttpException(e)


@router.post("/{transformation_id}/rollback")
async def rollbackTransformation(
    transformation_id: str,
    projectId: str,
    request: RollbackRequest,
    token=Depends(verifyToken),
):
    """
    Rollback workspace state and messages to a specific message ID.
    """
    try:
        result = await transformationService.rollbackTransformation(
            projectId=projectId,
            transformationId=transformation_id,
            messageId=request.messageId,
        )
        return ORJSONResponse(status_code=200, content=result)
    except CustomException as e:
        raiseHttpException(e)
