from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import SpeechToTextModel
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from ..components.celery import task
from supabase import create_client
from typing import Annotated
from . import pipeline
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
    
@router.get("/getForecastStatus/{taskId}")
async def getForecastStatus(taskId: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            r = task.celeryApp.AsyncResult(taskId)
            if not r.ready():
                return JSONResponse(status_code = 200, content = {"taskId": r.task_id, "taskStatus": r.status})
            else:
                return JSONResponse(status_code = 200, content = {"taskId": r.task_id, "taskStatus": r.status, "taskResponseCode": r.get()})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")