from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from supabase import create_client
from typing import Annotated
from jose import jwt
import pandas as pd
import os

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.get("/createProject/{projectName}")
async def createProject(projectName: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            decodedToken = jwt.decode(
                credentials.credentials,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            response = client.table("Projects").insert({
                "projectName": projectName,
                "ownerUserId": decodedToken["userId"],
                "ownerUserMail": decodedToken["email"]
            }).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project created successfully"})
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

@router.post("/uploadData")
async def uploadData(projectId: Annotated[str, Form()], file: Annotated[UploadFile, File()], credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            project = client.table("Projects").select("projectId", "projectName", "dataTables").eq("projectId", projectId).execute().data[0]                
            response = client.storage.from_("AnalyticsHub").upload(
                file = await file.read(),
                path = f"{projectId}/{file.filename}"
            )
            if project["dataTables"]:
                projectData = project["dataTables"] + f", {file.filename}"
            else:
                projectData = file.filename 
            response = client.table("Projects").update({"dataTables": projectData}).eq("projectId", projectId).execute()
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.post("/addBookmark")
async def uploadData(projectId: Annotated[str, Form()], file: Annotated[UploadFile, File()], credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            project = client.table("Projects").select("projectId", "projectName", "dataTables").eq("projectId", projectId).execute().data[0]                
            response = client.storage.from_("AnalyticsHub").upload(
                file = await file.read(),
                path = f"{projectId}/{file.filename}"
            )
            if project["dataTables"]:
                projectData = project["dataTables"] + f", {file.filename}"
            else:
                projectData = file.filename 
            response = client.table("Projects").update({"dataTables": projectData}).eq("projectId", projectId).execute()
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")