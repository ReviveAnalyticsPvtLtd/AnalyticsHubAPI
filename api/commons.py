"""
commons.py

This module sets up the Supabase client, security dependencies, and provides token verification logic for authentication-protected routes.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["client", "verifyToken", "updateProjectModifiedAt"] 

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

def verifyToken(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify the provided access token against the Sessions table.

    Args:
        credentials (HTTPAuthorizationCredentials): The HTTP bearer token credentials (injected by FastAPI).

    Returns:
        str: The valid access token if found and updated.

    Raises:
        HTTPException: If the token is invalid or expired.
    """
    gc.collect()
    token = credentials.credentials
    response = client.table("Sessions").select("*").eq("accessToken", token).limit(1).execute()
    if response.data:
        client.table("Sessions").update({"lastActivity": str(datetime.now())}).eq("accessToken", token).execute()
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return token

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