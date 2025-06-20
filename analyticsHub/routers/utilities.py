from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import SpeechToTextModel
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from ..components.celery import task
from supabase import create_client
from typing import Annotated
from . import pipeline
import asyncio
import os

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.post("/getSpeechTranscript")
async def getSpeechTranscript(speechToText: SpeechToTextModel, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            transcriptText = pipeline.speechToText(b64String = speechToText.b64String)
            return JSONResponse(status_code = 200, content = {"transcriptionText": transcriptText})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.get("/sendForecasts")
async def sendForecasts(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            r = task.sendForecasts.delay()
            return JSONResponse(status_code = 200, content = {"taskId": r.task_id, "taskStatus": r.status})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.websocket("/ws/getTaskStatus")
async def getTaskStatus(websocket: WebSocket):
    authHeader = websocket.headers.get("authorization")
    if not authHeader or not authHeader.startswith("Bearer"):
        await websocket.close(code = status.WS_1008_POLICY_VIOLATION)
        return 
    token = int(authHeader.split(" ")[1])
    if not verifyToken(token = token):
        await websocket.close(code = status.WS_1008_POLICY_VIOLATION)
        return 
    await websocket.accept()
    try:
        taskId = websocket.query_params.get("taskId")
        r = task.celeryApp.AsyncResult(taskId)
        while not r.ready():
            asyncio.sleep(5)
            continue            
        await websocket.send_json({
            "taskId": r.task_id,
            "taskStatus": r.status,
            "taskResponseCode": r.get()
        })
        await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_json({"status": "ERROR", "errorDetail": str(e)})
        await websocket.close()