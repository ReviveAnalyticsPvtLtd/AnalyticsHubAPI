from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import SpeechToTextModel, CreateDataBlend
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from urllib.request import urlopen
from supabase import create_client
from typing import Annotated
from . import pipeline
import json
import os
import io

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
    
@router.post("/createDataBlend")
async def createDataBlend(blendDetails: CreateDataBlend, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            joinConfig = {
                "tables": blendDetails.tables,
                "joinTypes": blendDetails.joinTypes
            }
            project = client.table("Projects").select("projectId", "projectName", "dataTables").eq("projectId", blendDetails.projectId).execute().data[0]
            if "blendConfig.json" in [x.get("name") for x in client.storage.from_("AnalyticsHub").list(path = blendDetails.projectId)]:
                fileUrl = os.environ["FILE_URL"].format(projectId = blendDetails.projectId, fileName = "blendConfig.json").replace(".parquet", "")
                blendConfig = json.loads(urlopen(fileUrl).read())
                blendConfig[blendDetails.blendName] = joinConfig
            else:
                blendConfig = {blendDetails.blendName: joinConfig}
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(blendConfig, indent=4).encode("utf-8"))
                buffer.seek(0)
                _ = client.storage.from_("AnalyticsHub").upload(path = f"{blendDetails.projectId}/blendConfig.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            if project["dataTables"]:
                projectData = project["dataTables"] + f", {blendDetails.blendName}"
            else:
                projectData = blendDetails.blendName
            _ = client.table("Projects").update({"dataTables": projectData}).eq("projectId", blendDetails.projectId).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Blend created successfully."})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")