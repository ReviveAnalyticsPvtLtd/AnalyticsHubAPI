from ..models.requestModels import UpdateProjectState, CreateProject, EditMetadata
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from supabase import create_client
from urllib.request import urlopen
from typing import Annotated
from . import pipeline
from jose import jwt
import pandas as pd
import datetime
import uuid
import json
import time
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
            files = client.storage.from_("AnalyticsHub").list(projectId)
            filenames = [x.get("name") for x in files]
            if "metadata.json" in filenames:
                fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
                jsonData = json.loads(urlopen(fileUrl).read())
                newMetadata = pipeline.generateMetadata(projectId = projectId)
                dataFiles = list()
                for file in files:
                    if file["name"] == "metadata.json":
                        metadataLastModifiedTime = datetime.datetime.strptime(file["metadata"]["lastModified"], '%Y-%m-%dT%H:%M:%S.000Z')
                    elif os.path.splitext(file["name"])[-1] == ".parquet":
                        dataFiles.append(file)
                    else:
                        continue
                updatedFiles = filter(lambda x: datetime.datetime.strptime(x["metadata"]["lastModified"], '%Y-%m-%dT%H:%M:%S.000Z') > metadataLastModifiedTime, dataFiles)
                updatedFiles = [x.get("name") for x in updatedFiles]
                for key in updatedFiles: jsonData[key] = newMetadata[key]
            else:
                jsonData = pipeline.generateMetadata(projectId = projectId)
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(jsonData, indent=4).encode("utf-8"))
                buffer.seek(0)
                client.storage.from_("AnalyticsHub").upload(path = f"{projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "metadata": jsonData})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/getMetadata/{projectId}")
async def getMetadata(projectId: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
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

@router.put("/editMetadata")
async def editMetadata(modifiedMetadata: EditMetadata, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            fileUrl = os.environ["FILE_URL"].format(projectId = modifiedMetadata.projectId, fileName = "metadata.json").replace(".parquet", "") + f"?cb={int(time.time())}"
            jsonData = json.loads(urlopen(fileUrl).read())
            if modifiedMetadata.tableDescription and not (modifiedMetadata.columnName or modifiedMetadata.columnDescription):
                jsonData[modifiedMetadata.tableName]["description"] = modifiedMetadata.tableDescription
            elif (modifiedMetadata.columnName and modifiedMetadata.columnDescription) and not modifiedMetadata.tableDescription:
                columns = jsonData[modifiedMetadata.tableName]["columns"]
                for column in columns:
                    if column["name"] == modifiedMetadata.columnName:
                        idx = columns.index(column)
                columns[idx]["description"] = modifiedMetadata.columnDescription
                jsonData[modifiedMetadata.tableName]["columns"] = columns
            else:
                raise AttributeError("Invalid combination of parameters provided")
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(jsonData, indent=4).encode("utf-8"))
                buffer.seek(0)
                client.storage.from_("AnalyticsHub").upload(path = f"{modifiedMetadata.projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "metadata": jsonData})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.delete("/deleteProject")
async def deleteProject(projectId: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            _ = client.table("Projects").delete().eq("projectId", projectId).execute()
            allFiles = client.storage.from_("AnalyticsHub").list(projectId)
            fileNames = [os.path.join(projectId, x.get("name")) for x in allFiles]
            _ = client.storage.from_("AnalyticsHub").remove(fileNames)
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project deleted successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")