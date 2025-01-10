from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import UploadData
from fastapi import APIRouter, Depends, Form
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
                credentials.credentials.split(" ")[1],
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
                credentials.credentials.split(" ")[1],
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            data = pd.DataFrame(client.table("Projects").select("projectId", "projectName", "ownerUserId").execute().data, columns = ["projectId", "projectName", "ownerUserId"])
            data = data[data["ownerUserId"] == decodedToken["userId"]]
            projects = list()
            for projectId, projectName in zip(data["projectId"], data["projectName"]):
                projects.append({"projectId": projectId, "projectName": projectName})
            return JSONResponse(status_code = 200, content = {"projects": projects})
        else:
            JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.post("/uploadData")
async def uploadData(dataInfo: Annotated[UploadData, Form()], credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            project = client.table("Projects").select("projectId", "projectName", "dataTables").eq("projectId", dataInfo.dataFile.projectId).execute().data[0]                
            response = client.storage.from_("AnalyticsHub").upload(
                file = await dataInfo.dataFile.read(),
                path = f"{dataInfo.projectId}/{dataInfo.dataFile.filename}"
            )
            if project["dataTables"]:
                projectData = project["dataTables"] + f", {dataInfo.dataFile.filename}"
            else:
                projectData = dataInfo.dataFile.filename 
            response = client.table("Projects").update({"dataTables": projectData}).eq("projectId", dataInfo.projectId).execute()
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")