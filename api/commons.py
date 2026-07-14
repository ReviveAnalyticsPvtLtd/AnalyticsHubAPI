"""
commons.py

This module sets up the Supabase client, security dependencies, and provides token verification logic for authentication-protected routes.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = [
    "client",
    "verifyToken",
    "verifyTokenWithExpiry",
    "verifyUser",
    "requireActiveSubscription",
    "requireTrialOrAbove",
    "requirePaidPlan",
    "requireCredits",
    "UserContext",
    "updateProjectModifiedAt",
    "verifyProjectOwnership",
    "verifyProjectOwnershipDirect",
    "requireTenantSlot",
    "releaseTenantSlot",
]

from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import status, HTTPException, Request, Depends
from supabase.lib.client_options import ClientOptions
from supabase import create_client
from utils.logger import logger
from utils.exceptionHandler import raiseFeatureGateHttpException
from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import redis
import os
import gc
import threading

security = HTTPBearer()

_redis_pool = None

def get_redis_client() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            password=os.environ["REDIS_PASSWORD"],
        )
    return redis.Redis(connection_pool=_redis_pool)

_client_lock = threading.Lock()

class SupabaseClientProxy:
    def __init__(self):
        self._instance = None

    def _get_instance(self):
        global _client_lock
        if self._instance is None:
            with _client_lock:
                if self._instance is None:
                    self._instance = create_client(
                        supabase_url = os.environ["SUPABASE_URL"],
                        supabase_key = os.environ["SUPABASE_KEY"],
                        options = ClientOptions(
                            auto_refresh_token = False,
                            persist_session = False,
                        )
                    )
        return self._instance

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)

    def __dir__(self):
        return dir(self._get_instance())

client = SupabaseClientProxy()

from jose import jwt, JWTError

async def _verifyTokenInternal(credentials: HTTPAuthorizationCredentials, checkExpiry: bool) -> str:
    """
    Internal helper to verify the token, cross-validate with JWT payload, and optionally check for expiry.
    Async so FastAPI runs it on the event loop — no threadpool thread consumed.
    Only the blocking Supabase HTTP calls are dispatched to a thread.
    """
    token = credentials.credentials

    # 1. Decode JWT to verify integrity and extract email (CPU-only, no thread needed)
    try:
        payload = jwt.decode(token, os.environ["SECRET_KEY"], algorithms=["HS256"])
        tokenEmail = payload.get("email")
        if not tokenEmail:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "FAILURE", "message": "Invalid token payload: missing email"}
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "FAILURE", "message": "Invalid token: failed to decode"}
        )

    # 2. Check existence in database / cache
    sessionData = None
    r = None
    try:
        r = get_redis_client()
        cached = r.get(f"session:{token}")
        if cached:
            import json
            sessionData = json.loads(cached)
    except Exception as e:
        logger.warning(f"Failed to read session cache from Redis: {e}")

    if not sessionData:
        response = await asyncio.to_thread(
            lambda: client.table("Sessions").select("*").eq("accessToken", token).limit(1).execute()
        )
        if not response.data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "FAILURE", "message": "Invalid or expired token"}
            )
        sessionData = response.data[0]
        try:
            if r:
                import json
                r.setex(f"session:{token}", 60, json.dumps(sessionData))
        except Exception as e:
            logger.warning(f"Failed to write session cache to Redis: {e}")

    dbEmail = sessionData.get("email")

    # 3. Cross-validate email (JWT vs DB)
    if tokenEmail != dbEmail:
        logger.error(f"Token email mismatch: JWT({tokenEmail}) vs DB({dbEmail})")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "FAILURE", "message": "Token validation failed: identity mismatch"}
        )

    # 4. Optional Expiry Check
    if checkExpiry:
        expiresAtStr = sessionData.get("expiresAt")
        if expiresAtStr:
            try:
                from dateutil import parser
                expiresAt = parser.parse(expiresAtStr)
                if expiresAt.tzinfo is None:
                    expiresAt = expiresAt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expiresAt:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={"status": "FAILURE", "message": "Token has expired"}
                    )
            except ValueError:
                logger.error(f"Failed to parse expiry time: {expiresAtStr}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"status": "FAILURE", "message": "Invalid token expiry format"}
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "FAILURE", "message": "Token expiry information missing"}
            )

    # 5. Buffer lastActivity in Redis instead of writing to database on every request
    try:
        if r:
            r.hset("session:last_activity", token, str(datetime.now(timezone.utc)))
    except Exception as e:
        logger.warning(f"Failed to buffer lastActivity in Redis: {e}")
    return token

async def verifyToken(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify the provided access token against the Sessions table.
    """
    return await _verifyTokenInternal(credentials, checkExpiry=False)

async def verifyTokenWithExpiry(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify the provided access token and ensure it has not expired.
    """
    return await _verifyTokenInternal(credentials, checkExpiry=True)


@dataclass
class UserContext:
    """Decoded JWT claims including subscription state."""
    userId: str
    email: str
    token: str
    sub_status: str
    plan_type: str


async def verifyUser(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UserContext:
    """
    Verify the token and return a UserContext with subscription claims
    decoded from the JWT. No additional DB query for subscription data.
    """
    token = await _verifyTokenInternal(credentials, checkExpiry=False)
    payload = jwt.decode(token, os.environ["SECRET_KEY"], algorithms=["HS256"])
    return UserContext(
        userId=payload["userId"],
        email=payload["email"],
        token=token,
        sub_status=payload.get("sub_status", "none"),
        plan_type=payload.get("plan_type", "none"),
    )


_ACTIVE_SUBSCRIPTION_STATUSES = {"active", "renewal_upcoming", "payment_pending", "cancelled"}

async def requireActiveSubscription(
    user: UserContext = Depends(verifyUser),
) -> UserContext:
    """Gate: requires an active (or cancelled-but-in-period) subscription."""
    if user.sub_status not in _ACTIVE_SUBSCRIPTION_STATUSES:
        raiseFeatureGateHttpException(
            statusCode=status.HTTP_403_FORBIDDEN,
            uiMessage="This feature requires an active subscription.",
            backendLogMessage=(
                f"Feature gate requireActiveSubscription blocked userId={user.userId}, "
                f"sub_status={user.sub_status}, "
                f"allowed={sorted(_ACTIVE_SUBSCRIPTION_STATUSES)}"
            ),
            errorCode="FEATURE_BLOCKED",
        )
    return user


_TRIAL_OR_ABOVE_STATUSES = {"trial", "active", "renewal_upcoming", "payment_pending", "cancelled"}

async def requireTrialOrAbove(
    user: UserContext = Depends(verifyUser),
) -> UserContext:
    """Gate: requires at least a trial subscription."""
    if user.sub_status not in _TRIAL_OR_ABOVE_STATUSES:
        raiseFeatureGateHttpException(
            statusCode=status.HTTP_403_FORBIDDEN,
            uiMessage="Start a free trial or subscribe to access this feature.",
            backendLogMessage=(
                f"Feature gate requireTrialOrAbove blocked userId={user.userId}, "
                f"sub_status={user.sub_status}, "
                f"allowed={sorted(_TRIAL_OR_ABOVE_STATUSES)}"
            ),
            errorCode="FEATURE_BLOCKED",
        )
    return user


async def requirePaidPlan(
    user: UserContext = Depends(verifyUser),
) -> UserContext:
    """Gate: requires a paid plan (pro or annual)."""
    if user.plan_type in ("none", "free"):
        raiseFeatureGateHttpException(
            statusCode=status.HTTP_403_FORBIDDEN,
            uiMessage="This feature requires a paid plan.",
            backendLogMessage=(
                f"Feature gate requirePaidPlan blocked userId={user.userId}, "
                f"plan_type={user.plan_type}, sub_status={user.sub_status}"
            ),
            errorCode="FEATURE_BLOCKED",
        )
    return user


def requireCredits(operationType: str):
    """
    Dependency factory that returns a FastAPI dependency checking whether
    the user has enough credits for the specified operation type.

    Usage::

        @router.post("/generateChart")
        async def generateChart(body: ..., user=Depends(requireCredits("reporting_query"))):
            ...

    Raises HTTPException 402 when the user's remaining credits are below
    the configured minimum for the operation.
    """
    async def _dependency(user: UserContext = Depends(verifyUser)) -> UserContext:
        from api.services.credits.creditService import creditService
        from api.services.credits.creditConfig import getOperationMinimum

        minimum = getOperationMinimum(operationType)
        effective = await asyncio.to_thread(creditService.getRemainingCredits, user.userId)

        if effective != -1 and effective < minimum:
            snapshot = await asyncio.to_thread(creditService.getBalanceSnapshot, user.userId)
            monthlyRemaining = snapshot.get("remainingCredits", 0)

            if monthlyRemaining < minimum:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "status": "FAILURE",
                        "message": (
                            "Monthly quota exhausted. Your credits reset at the start "
                            f"of your next billing period ({snapshot.get('periodEnd')})."
                        ),
                        "errorCode": "MONTHLY_QUOTA_EXHAUSTED",
                        "remaining": effective,
                        "required": minimum,
                        "resetAt": snapshot.get("periodEnd"),
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "status": "FAILURE",
                    "message": (
                        "Daily credit limit reached. Your daily allowance resets at "
                        f"00:00 UTC ({snapshot.get('dailyResetAt')})."
                    ),
                    "errorCode": "DAILY_LIMIT_REACHED",
                    "remaining": effective,
                    "required": minimum,
                    "resetAt": snapshot.get("dailyResetAt"),
                },
            )
        return user

    return _dependency


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


async def verifyProjectOwnershipDirect(projectId: str, userId: str) -> None:
    """Decode userId from JWT, then confirm project belongs to that user."""
    response = await asyncio.to_thread(
        lambda: client.table("Projects").select("ownerUserId").eq("projectId", projectId).execute()
    )
    if not response.data or response.data[0].get("ownerUserId") != userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "FAILURE", "message": "Access denied: you do not own this project"}
        )


async def verifyProjectOwnership(projectId: str, user: UserContext = Depends(verifyUser)) -> str:
    """FastAPI dependency to verify project ownership for path/query parameters."""
    await verifyProjectOwnershipDirect(projectId, user.userId)
    return user.userId


# --- Per-tenant concurrency caps -------------------------------------------

_TENANT_MAX_CONCURRENT = int(os.environ.get("TENANT_MAX_CONCURRENT", "5"))


def _tenant_redis():
    """Lazy Redis connection for tenant semaphores."""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        password=os.environ.get("REDIS_PASSWORD"),
        db=int(os.environ.get("REDIS_SEMAPHORE_DB", "1")),
    )


async def requireTenantSlot(user: UserContext = Depends(verifyUser)) -> UserContext:
    """FastAPI dependency: acquire a per-tenant concurrency slot.
    Raises 429 if the tenant already has too many in-flight heavy requests.
    Always pair with `releaseTenantSlot` in a finally block.
    """
    r = _tenant_redis()
    key = f"conc:{user.userId}"
    current = await asyncio.to_thread(lambda: r.incr(key))
    if current > _TENANT_MAX_CONCURRENT:
        await asyncio.to_thread(lambda: r.decr(key))
        raise HTTPException(
            status_code=429,
            detail={"status": 429, "message": "Too many concurrent requests. Please wait for in-flight operations to complete."}
        )
    await asyncio.to_thread(lambda: r.expire(key, 120))
    return user


def releaseTenantSlot(userId: str) -> None:
    """Release a previously acquired tenant concurrency slot."""
    try:
        r = _tenant_redis()
        r.decr(f"conc:{userId}")
    except Exception:
        pass