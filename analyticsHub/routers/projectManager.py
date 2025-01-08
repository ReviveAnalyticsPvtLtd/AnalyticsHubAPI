from ..models.requestModels import CreateProject
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from fastapi import APIRouter, Header
from supabase import create_client
from typing import Annotated
from jose import jwt
import os

router = APIRouter()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.get("/createProject")
async def createProject(projectDetails: CreateProject, token: Annotated[str, Header()]):
    try:
        if verifyToken(token = token):
            decodedToken = jwt.decode(
                token.split(" ")[1],
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            response = client.table("Projects").insert({
                "projectName": projectDetails.projectName,
                "ownerUserId": decodedToken["userId"],
                "ownerUserMail": decodedToken["email"]
            }).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Project created successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    