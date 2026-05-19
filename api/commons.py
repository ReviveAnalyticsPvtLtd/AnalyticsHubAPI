"""
commons.py

This module sets up the Supabase client, security dependencies, and provides token verification logic for authentication-protected routes.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["client", "verifyToken", "verifyTokenWithExpiry", "updateProjectModifiedAt"] 

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import status, HTTPException
from supabase.lib.client_options import ClientOptions
from supabase import create_client
from utils.logger import logger
from datetime import datetime, timezone
from fastapi import Depends
import os
import gc

security = HTTPBearer()
client = create_client(
    supabase_url = os.environ["SUPABASE_URL"],
    supabase_key = os.environ["SUPABASE_KEY"],
    options = ClientOptions(
        auto_refresh_token = False,
        persist_session = False,
    )
)

from jose import jwt, JWTError

def _verifyTokenInternal(credentials: HTTPAuthorizationCredentials, checkExpiry: bool) -> str:
    """
    Internal helper to verify the token, cross-validate with JWT payload, and optionally check for expiry.
    """
    gc.collect()
    token = credentials.credentials
    
    # 1. Decode JWT to verify integrity and extract email
    try:
        payload = jwt.decode(token, os.environ["SECRET_KEY"], algorithms=["HS256"])
        tokenEmail = payload.get("email")
        if not tokenEmail:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload: missing email")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: failed to decode")

    # 2. Check existence in database
    response = client.table("Sessions").select("*").eq("accessToken", token).limit(1).execute()
    
    if not response.data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    
    sessionData = response.data[0]
    dbEmail = sessionData.get("email")

    # 3. Cross-validate email (JWT vs DB)
    if tokenEmail != dbEmail:
        logger.error(f"Token email mismatch: JWT({tokenEmail}) vs DB({dbEmail})")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token validation failed: identity mismatch")
    
    # 4. Optional Expiry Check
    if checkExpiry:
        expiresAtStr = sessionData.get("expiresAt")
        if expiresAtStr:
            try:
                # Replace space with T for ISO format compatibility if needed
                expiresAt = datetime.fromisoformat(expiresAtStr.replace(" ", "T"))
                if datetime.now(timezone.utc) > expiresAt:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
            except ValueError:
                logger.error(f"Failed to parse expiry time: {expiresAtStr}")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token expiry format")
        else:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expiry information missing")

    client.table("Sessions").update({"lastActivity": str(datetime.now(timezone.utc))}).eq("accessToken", token).execute()
    return token

def verifyToken(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify the provided access token against the Sessions table.
    """
    return _verifyTokenInternal(credentials, checkExpiry=False)

def verifyTokenWithExpiry(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify the provided access token and ensure it has not expired.
    """
    return _verifyTokenInternal(credentials, checkExpiry=True)

def updateProjectModifiedAt(projectId: str) -> None:
    """
    Update the modifiedAt field of a project to the current UTC timestamp.

    This function should be called at the end of any service method that
    mutates project data (e.g., loading data, editing metadata, creating
    dashboard pages, generating charts, etc.).

    Args:
        projectId (str): The project identifier whose modifiedAt field should be updated.
    """
    try:
        client.table("Projects").update({
            "modifiedAt": datetime.now(timezone.utc).isoformat()
        }).eq("projectId", projectId).execute()
    except Exception as e:
        logger.error(f"Failed to update modifiedAt for project {projectId}: {e}")