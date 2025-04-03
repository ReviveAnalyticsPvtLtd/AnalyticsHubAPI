from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import APIRouter, Depends, UploadFile, File, Form
from ..models.requestModels import DeleteTable
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from urllib.request import urlopen
from supabase import create_client
from fastapi import APIRouter
from typing import Annotated
import tempfile
import duckdb
import json
import io
import os

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.post("/loadCsvData")
async def loadCsvData(projectId: Annotated[str, Form()], file: Annotated[UploadFile, File()], credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            project = client.table("Projects").select("projectId", "projectName", "dataTables").eq("projectId", projectId).execute().data[0]                
            with tempfile.NamedTemporaryFile(delete = True, suffix = ".parquet") as temp:
                duckdb.read_csv(io.BytesIO(await file.read())).write_parquet(temp.name, compression = "snappy")
                _ = client.storage.from_("AnalyticsHub").upload(
                    file = temp.name,
                    path = f"{projectId}/{os.path.splitext(file.filename)[0] + '.parquet'}"
                )
                if project["dataTables"]:
                    projectData = project["dataTables"] + f", {os.path.splitext(file.filename)[0]}"
                else:
                    projectData = os.path.splitext(file.filename)[0]
                _ = client.table("Projects").update({"dataTables": projectData}).eq("projectId", projectId).execute()
                temp.close()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Data loaded successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.delete("/deleteTable")
async def deleteTable(tableDetails: DeleteTable, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            _ = client.storage.from_("AnalyticsHub").remove(f"{tableDetails.projectId}/{tableDetails.tableName}" + ".parquet")
            projectTables = client.table("Projects").select("dataTables").eq("projectId", tableDetails.projectId).execute().data[0]["dataTables"]
            projectTables = projectTables.split(", ")
            projectTables.remove(tableDetails.tableName)
            projectTables = ", ".join(projectTables)
            _ = client.table("Projects").update({"dataTables": projectTables}).eq("projectId", tableDetails.projectId).execute()
            fileUrl = os.environ["FILE_URL"].format(projectId = tableDetails.projectId, fileName = "metadata.json").replace(".parquet", "")
            jsonData = json.loads(urlopen(fileUrl).read())
            jsonData.pop(tableDetails.tableName)
            with io.BytesIO() as buffer:
                buffer.write(json.dumps(jsonData, indent=4).encode("utf-8"))
                buffer.seek(0)
                client.storage.from_("AnalyticsHub").upload(path = f"{tableDetails.projectId}/metadata.json", file = buffer.getvalue(), file_options = {"upsert": "true"})
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Table deleted successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})    
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
