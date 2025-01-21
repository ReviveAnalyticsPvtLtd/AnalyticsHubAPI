from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ..models.requestModels import SignUp, Login, LoginWithProvider
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from ..utils.functions import verifyToken
from supabase import create_client
from typing import Annotated
from jose import jwt
import pandas as pd
import datetime
import hashlib
import os

router = APIRouter()
security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"]
)

@router.post("/signUp")
async def signup(signupDetails: SignUp):
    try:
        passwordString = signupDetails.password + os.environ["SECRET_KEY"]
        hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
        allData = pd.DataFrame(client.table("Users").select("userId", "email", "password").execute().data, columns = ["userId", "email", "password"])
        if signupDetails.email not in allData["email"]:
            response = client.table(table_name = "Users").insert(
                {
                    "email": signupDetails.email,
                    "password": hashedPassword
                }
            ).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS"})
        else:
            return JSONResponse(status_code = 409, content = {"status": "ERROR", "errorDetail": "User Already Exists"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.post("/login")
async def login(loginDetails: Login):
    try:
        passwordString = loginDetails.password + os.environ["SECRET_KEY"]
        hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
        allData = pd.DataFrame(client.table("Users").select("userId", "email", "password").execute().data, columns = ["userId", "email", "password"])
        if loginDetails.email not in allData["email"].unique():
            return JSONResponse(status_code = 404, content = {"status": "ERROR", "errorDetail": "User not found"})
        else:  
            dataSlice = allData[allData["email"] == loginDetails.email].iloc[0, :]
            if dataSlice["password"] != hashedPassword:
                return JSONResponse(status_code = 401, content = {"status": "ERROR", "errorDetail": "Invalid email or password"})
            else:
                sessionStartTime = str(datetime.datetime.utcnow())
                dictItems = {
                    "userId": dataSlice["userId"],
                    "email": loginDetails.email,
                    "password": hashedPassword,
                    "sessionStartTime": sessionStartTime
                }
                accessToken = jwt.encode(dictItems, os.environ["SECRET_KEY"], "HS256")
                client.table("Sessions").insert({
                    "userId": dataSlice["userId"],
                    "email": dataSlice["email"],
                    "accessToken": accessToken,
                    "sessionStartTime": sessionStartTime,
                    "lastActivity": sessionStartTime
                }).execute()
                response = {
                    "status": "SUCCESS",
                    "userId": dataSlice["userId"],
                    "email": dataSlice["email"],
                    "accessToken": accessToken
                }
                return JSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.post("/loginWithProvider")
async def loginWithProvider(loginDetails: LoginWithProvider):
    try:
        passwordString = loginDetails.sub + loginDetails.id + loginDetails.nodeId + os.environ["SECRET_KEY"]
        hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
        registeredUsers = pd.DataFrame(client.table("Users").select("email", "password").execute().data, columns = ["email", "password"])
        if loginDetails.email not in registeredUsers["email"]:
            response = client.table(table_name = "Users").insert(
                {
                    "email": loginDetails.email,
                    "password": hashedPassword
                }
            ).execute()
        else:
            pass
        dataSlice = registeredUsers[registeredUsers["email"] == loginDetails.email].iloc[0, :]
        sessionStartTime = str(datetime.datetime.utcnow())
        dictItems = {
            "userId": dataSlice["userId"],
            "email": loginDetails.email,
            "password": hashedPassword,
            "sessionStartTime": sessionStartTime
        }
        accessToken = jwt.encode(dictItems, os.environ["SECRET_KEY"], "HS256")
        client.table("Sessions").insert({
            "userId": dataSlice["userId"],
            "email": dataSlice["email"],
            "accessToken": accessToken,
            "sessionStartTime": sessionStartTime,
            "lastActivity": sessionStartTime
        }).execute()
        response = {
            "status": "SUCCESS",
            "userId": dataSlice["userId"],
            "email": dataSlice["email"],
            "accessToken": accessToken
        }
        return JSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.get("/logout")
async def logout(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            response = client.table("Sessions").delete().eq("accessToken", credentials.credentials.split(" ")[1]).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Session logged out successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")