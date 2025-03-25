from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import SpeechToTextModel
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from typing import Annotated
from . import pipeline

router = APIRouter()
security = HTTPBearer()

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