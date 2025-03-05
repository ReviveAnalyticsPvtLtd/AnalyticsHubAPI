from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import UpdateProjectState, CreateProject
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from supabase import create_client
from typing import Annotated
from jose import jwt
import pandas as pd
import uuid
import os

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
            decodedToken = jwt.decode(
                credentials.credentials,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            response = client.table("Projects").insert({
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
            JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.patch("/updateBookmark")
async def updateBookmark(updateBookmarkDetails: UpdateProjectState, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if updateBookmarkDetails.action == "add":
                response = client.table("Projects").update({"isBookmarked": 1}).eq("projectId", updateBookmarkDetails.projectId).execute()
            else:
                response = client.table("Projects").update({"isBookmarked": 0}).eq("projectId", updateBookmarkDetails.projectId).execute()                
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project bookmark status updated successfully"})
        else:
            JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.patch("/updateArchive")
async def updateArchive(updateArchiveDetails: UpdateProjectState, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if updateArchiveDetails.action == "add":
                response = client.table("Projects").update({"isArchived": 1}).eq("projectId", updateArchiveDetails.projectId).execute()
            else:
                response = client.table("Projects").update({"isArchived": 0}).eq("projectId", updateArchiveDetails.projectId).execute()                
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project archive status updated successfully"})
        else:
            JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.patch("/updateTrash")
async def updateTrash(updateTrashDetails: UpdateProjectState, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if updateTrashDetails.action == "add":
                response = client.table("Projects").update({"isTrash": 1}).eq("projectId", updateTrashDetails.projectId).execute()
            else:
                response = client.table("Projects").update({"isTrash": 0}).eq("projectId", updateTrashDetails.projectId).execute()                
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project trash status updated successfully"})
        else:
            JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")