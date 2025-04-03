from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import UpdateProjectState, CreateProject
from langchain_experimental.utilities import PythonREPL
from ..utils.functions import verifyToken, readYaml
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..components import replManager
from fastapi import APIRouter, Depends
from supabase import create_client
from urllib.request import urlopen
from typing import Annotated
from . import pipeline
from jose import jwt
import pandas as pd
import uuid
import json
import os
import io

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.post("/createProject")
async def createProject(projectDetails: CreateProject, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            projectId = str(uuid.uuid4())
            replManager.manager[projectId] = PythonREPL()
            _ = replManager.manager[projectId].run(readYaml("params.yaml")["redisFunctionCode"])
            _ = replManager.manager[projectId].run(readYaml("params.yaml")["jsonSerializer"])
            _ = replManager.manager[projectId].run(("globals()['__name__'] = '__main__'"))
            _ = replManager.manager[projectId].run("globals().update(locals())")
            decodedToken = jwt.decode(
                credentials.credentials,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            _ = client.table("Projects").insert({
                "projectId": projectId,
                "projectName": projectDetails.projectName,
                "projectDescription": projectDetails.projectDescription,
                "ownerUserId": decodedToken["userId"],
                "ownerUserMail": decodedToken["email"]
            }).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "projectId": projectId, "message": "Project created successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.get("/listProjects")
async def listProjects(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            decodedToken = jwt.decode(
                credentials.credentials,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            data = pd.DataFrame(client.table("Projects").select("*").execute().data)
            data = data[data["ownerUserId"] == decodedToken["userId"]]
            return JSONResponse(status_code = 200, content = {"projects": data.to_dict(orient = "records")})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.patch("/updateBookmark")
async def updateBookmark(updateBookmarkDetails: UpdateProjectState, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if updateBookmarkDetails.action == "add":
                _ = client.table("Projects").update({"isBookmarked": 1}).eq("projectId", updateBookmarkDetails.projectId).execute()
            else:
                _ = client.table("Projects").update({"isBookmarked": 0}).eq("projectId", updateBookmarkDetails.projectId).execute()                
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project bookmark status updated successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.patch("/updateArchive")
async def updateArchive(updateArchiveDetails: UpdateProjectState, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if updateArchiveDetails.action == "add":
                _ = client.table("Projects").update({"isArchived": 1}).eq("projectId", updateArchiveDetails.projectId).execute()
            else:
                _ = client.table("Projects").update({"isArchived": 0}).eq("projectId", updateArchiveDetails.projectId).execute()                
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project archive status updated successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.patch("/updateTrash")
async def updateTrash(updateTrashDetails: UpdateProjectState, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if updateTrashDetails.action == "add":
                _ = client.table("Projects").update({"isTrash": 1}).eq("projectId", updateTrashDetails.projectId).execute()
            else:
                _ = client.table("Projects").update({"isTrash": 0}).eq("projectId", updateTrashDetails.projectId).execute()                
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project trash status updated successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.post("/generateMetadata/{projectId}")
async def generateMetadata(projectId: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            metadata = pipeline.generateMetadata(projectId = projectId)
            _ = replManager.manager[projectId].run(f'metadata = {metadata}')
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(metadata, indent=4).encode("utf-8"))
                buffer.seek(0)
                client.storage.from_("AnalyticsHub").upload(path = f"{projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "metadata": metadata})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/getMetadata/{projectId}")
async def getMetadata(projectId: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "")
            jsonData = json.loads(urlopen(fileUrl).read())
            newJson = {"tables": []}
            for key in jsonData:
                tableJson = {
                    "tableName": key,
                    "tableDesc": jsonData.get(key).get("description"),
                    "shape": jsonData.get(key).get("shape"),
                    "columns": jsonData.get(key).get("columns")
                }
                newJson.get("tables").append(tableJson)
            return JSONResponse(status_code = 200, content = newJson)
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")