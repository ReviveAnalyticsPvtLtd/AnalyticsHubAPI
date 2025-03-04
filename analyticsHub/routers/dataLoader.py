from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from supabase import create_client
from fastapi import APIRouter
from typing import Annotated
import tempfile
import duckdb
import io
import os

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.post("/loadCsvData")
async def uploadData(projectId: Annotated[str, Form()], file: Annotated[UploadFile, File()], credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            project = client.table("Projects").select("projectId", "projectName", "dataTables").eq("projectId", projectId).execute().data[0]                
            with tempfile.NamedTemporaryFile(delete = True, suffix = ".parquet") as temp:
                duckdb.read_csv(await file.read()).write_parquet(temp.name, compression = "snappy")
                response = client.storage.from_("AnalyticsHub").upload(
                    file = temp.name,
                    path = f"{projectId}/{os.path.splitext(file.filename)[0] + '.parquet'}"
                )
                if project["dataTables"]:
                    projectData = project["dataTables"] + f", {os.path.splitext(file.filename)[0]}"
                else:
                    projectData = os.path.splitext(file.filename)[0]
                response = client.table("Projects").update({"dataTables": projectData}).eq("projectId", projectId).execute()
                temp.close()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Data loaded successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")