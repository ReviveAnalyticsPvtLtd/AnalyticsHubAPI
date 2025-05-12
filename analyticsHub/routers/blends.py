from ..models.requestModels import CreateDataBlend, GetFieldsFromSources
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Depends
from urllib.request import urlopen
from supabase import create_client
from typing import Annotated
import json
import os
import io

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)
    
@router.post("/createDataBlend")
async def createDataBlend(blendDetails: CreateDataBlend, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            joinConfig = {
                "tables": blendDetails.tables,
                "blendOn": blendDetails.blendOn,
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
            if blendDetails.blendName in project["dataTables"].split(", "):
                pass
            elif project["dataTables"]:
                projectData = project["dataTables"] + f", {blendDetails.blendName}"
                _ = client.table("Projects").update({"dataTables": projectData}).eq("projectId", blendDetails.projectId).execute()
            else:
                projectData = blendDetails.blendName
                _ = client.table("Projects").update({"dataTables": projectData}).eq("projectId", blendDetails.projectId).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Blend created successfully."})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/getDataSources")
async def getDataSources(projectId: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            if "blendConfig.json" in [x.get("name") for x in client.storage.from_("AnalyticsHub").list(path = projectId)]:
                blendConfigUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "blendConfig.json").replace(".parquet", "")
                blendConfig = json.loads(urlopen(blendConfigUrl).read())
                blendedTables = list(blendConfig.keys())
                blends = [
                    {"blendName": x, "tables": blendConfig[x].get("tables"), "joinTypes": blendConfig[x].get("joinTypes"), "blendOn": blendConfig[x].get("blendOn")} for x in blendedTables
                ]
            else:
                blends, blendedTables = list(), list()
            metadataUrl = os.environ["FILE_URL"].format(projectId = projectId, fileName = "metadata.json").replace(".parquet", "")
            metadata = json.loads(urlopen(metadataUrl).read())
            rawTables = list(metadata.keys())
            dataSources = {
                "blends": blends,
                "rawTables": rawTables,
                "blendedTables": blendedTables
            }
            return JSONResponse(status_code = 200, content = dataSources)
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/getFieldsFromSources")
async def getFieldsFromSources(details: GetFieldsFromSources, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            blendConfigUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "blendConfig.json").replace(".parquet", "")
            metadataUrl = os.environ["FILE_URL"].format(projectId = details.projectId, fileName = "metadata.json").replace(".parquet", "")
            blendConfig = json.loads(urlopen(blendConfigUrl).read())
            metadata = json.loads(urlopen(metadataUrl).read())
            blendedTables = list(blendConfig.keys())
            allFields = list()
            if details.tableName in blendedTables:
                tablesUsed = blendConfig[details.tableName].get("tables")
                for table in tablesUsed:
                    allFields.extend(metadata[table]["columns"])
            else:
                allFields = metadata[details.tableName]["columns"]
            numericals = ["int64", "float64", "float32", "int32"]
            categoricals = ["bool", "category", "object", "string"]
            datetimeTypes = ["datetime64[ns]", "datetime64[ns, tz]"]
            numericalColumns, categoricalColumns, datetimeColumns = list(), list(), list()
            for column in allFields:
                if column.get("type") in categoricals:
                    categoricalColumns.append(column["name"])
                elif column["type"] in numericals:
                    numericalColumns.append(column["name"])
                elif column["type"] in datetimeTypes:
                    datetimeColumns.append(column["name"])
            response = {
                "numericalColumns": numericalColumns,
                "categoricalColumns": categoricalColumns,
                "datetimeColumns": datetimeColumns
            } 
            return JSONResponse(status_code = 200, content = response)
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})   
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")