from ..models.requestModels import SignUp, Login, LoginWithProvider, OnboardingDetails, NewCredentials
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import APIRouter, HTTPException, Depends
from supabase.lib.client_options import ClientOptions
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
    supabase_key = os.environ["SUPABASE_KEY"],
    options=ClientOptions(
        auto_refresh_token=False,
        persist_session=False,
    )
)

@router.post("/signUp")
async def signup(signupDetails: SignUp):
    try:
        passwordString = signupDetails.password + os.environ["SECRET_KEY"]
        hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
        allUsers = list()
        page = 1
        while True:
            response = client.auth.admin.list_users(page = page, per_page = 1000)
            if response == []:
                break
            else:
                allUsers.extend(response)
                page += 1
        allUsers = [x.email for x in allUsers]
        if signupDetails.email not in allUsers:
            response = client.auth.sign_up(
                {"email": signupDetails.email, "password": hashedPassword}
            )
            client.table(table_name = "Users").insert(
                {
                    "userId": response.user.id,
                    "email": signupDetails.email,
                    "password": hashedPassword
                }
            ).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "userId": response.user.id})
        else:
            return JSONResponse(status_code = 409, content = {"status": "ERROR", "errorDetail": "User Already Exists"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/confirmMail/{userId}")
async def confirmMail(userId: str):
    try:
        allUsers = list()
        page = 1
        while True:
            response = client.auth.admin.list_users(page = page, per_page = 1000)
            if response == []:
                break
            else:
                allUsers.extend(response)
                page += 1
        email = list(filter(lambda x: True if x.id == userId else False, allUsers))[0].email
        response = client.auth.resend({
        "type": "signup",
        "email": email,
        "options": {
            "email_redirect_to": "https://localhost:3000"
        }
        })
        return JSONResponse(status_code = 200, content = {"status": "SUCCESS"}) 
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.post("/login")
async def login(loginDetails: Login):
    try:
        passwordString = loginDetails.password + os.environ["SECRET_KEY"]
        hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
        allUsers = list()
        page = 1
        while True:
            response = client.auth.admin.list_users(page = page, per_page = 1000)
            if response == []:
                break
            else:
                allUsers.extend(response)
                page += 1
        filteredResult = list(filter(lambda x: True if x.email == loginDetails.email else False, allUsers))
        if filteredResult == []:
            return JSONResponse(status_code = 404, content = {"status": "ERROR", "errorDetail": "User not found"})
        elif filteredResult[0].user_metadata.get("email_verified") == False:
            return JSONResponse(status_code = 401, content = {"status": "ERROR", "errorDetail": "Email not verified"})
        else:  
            allData = pd.DataFrame(client.table("Users").select("userId", "email", "password", "onboarded").execute().data, columns = ["userId", "email", "password", "onboarded"])
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
                    "accessToken": accessToken,
                    "onboarded": int(dataSlice["onboarded"])
                }
                return JSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.post("/loginWithProvider")
async def loginWithProvider(loginDetails: LoginWithProvider):
    try:
        passwordString = str(loginDetails.sub) + str(loginDetails.id) + str(loginDetails.nodeId) + os.environ["SECRET_KEY"]
        hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
        registeredUsers = pd.DataFrame(client.table("Users").select("userId", "email", "password", "onboarded").execute().data, columns = ["userId", "email", "password", "onboarded"])
        if loginDetails.email not in registeredUsers["email"].unique():
            response = client.table(table_name = "Users").insert(
                {
                    "email": loginDetails.email,
                    "password": hashedPassword
                }
            ).execute()
            registeredUsers = pd.DataFrame(client.table("Users").select("userId", "email", "password", "onboarded").execute().data, columns = ["userId", "email", "password", "onboarded"])
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
            "accessToken": accessToken,
            "onboarded": int(dataSlice["onboarded"])
        }
        return JSONResponse(status_code = 200, content = response)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")
    
@router.post("/onboarding")
async def onboarding(onboardingDetails: OnboardingDetails, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            dataToUpdate = {
                "onboarded": 1,
                "usage": onboardingDetails.usage,
                "fullName": onboardingDetails.fullName,
                "role": onboardingDetails.role,
                "companyName": onboardingDetails.companyName,
                "industryType": onboardingDetails.industryType,
                "companySize": onboardingDetails.companySize,
                "country": onboardingDetails.country,
                "goals": onboardingDetails.goals,
                "source": onboardingDetails.source
            }
            response = client.table("Users").update(dataToUpdate).eq("email", onboardingDetails.email).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "User onboarded successfully."})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})        
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.put("/resetPassword")
async def resetPassword(email: str, newCredentials: NewCredentials):
    try:
        passwordString = newCredentials.newPassword + os.environ["SECRET_KEY"]
        hashedPassword = hashlib.md5(passwordString.encode("utf-8")).hexdigest()
        allUsers = list()
        page = 1
        while True:
            response = client.auth.admin.list_users(page = page, per_page = 1000)
            if response == []:
                break
            else:
                allUsers.extend(response)
                page += 1
        filteredResult = list(filter(lambda x: True if x.email == email else False, allUsers))[0]
        response = client.auth.admin.update_user_by_id(
            filteredResult.id,
            {"password": hashedPassword}
        )
        response = client.table("Users").update({"password": hashedPassword}).eq("email", email).execute()
        return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Password updated successfully!"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")

@router.get("/logout")
async def logout(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    try:
        if verifyToken(token = credentials.credentials):
            response = client.table("Sessions").delete().eq("accessToken", credentials.credentials).execute()
            return JSONResponse(status_code = 200, content = {"status": "SUCCESS", "message": "Session logged out successfully"})
        else:
            return JSONResponse(status_code = 498, content = {"status": "ERROR", "errorDetail": "Invalid Token"})
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Endpoint says: {e}")