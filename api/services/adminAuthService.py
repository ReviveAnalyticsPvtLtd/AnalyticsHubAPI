import hashlib
import hmac
import ipaddress
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from loguru import logger
from pwdlib import PasswordHash
from pydantic import EmailStr, TypeAdapter, ValidationError

from api.adminErrors import AdminApiError


ADMIN_TOKEN_ISSUER = "nubrix-admin"
ADMIN_TOKEN_AUDIENCE = "nubrix-admin-api"
ADMIN_TOKEN_HOURS = 8
ADMIN_LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)
ADMIN_EMAIL_FAILURE_LIMIT = 5
ADMIN_IP_FAILURE_LIMIT = 20
ADMIN_THROTTLE_WINDOW_SECONDS = 15 * 60


def _tokenDigest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _rateIdentity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdminContext:
    adminId: str
    email: str
    name: str
    sessionId: str
    token: str


class AdminAuthService:
    def __init__(self, client=None, passwordHasher=None, redisClient=None,
                 nowProvider: Callable[[], datetime] | None = None):
        self._client = client
        self.passwordHasher = passwordHasher or PasswordHash.recommended()
        self._redisClient = redisClient
        self.nowProvider = nowProvider or (lambda: datetime.now(timezone.utc))
        self._dummyPasswordHash = PasswordHash.recommended().hash(
            "nubrix-admin-dummy-password"
        )

    @property
    def client(self):
        if self._client is None:
            from api.commons import client
            self._client = client
        return self._client

    @property
    def redisClient(self):
        if self._redisClient is None:
            self._redisClient = redis.Redis(
                host=os.environ.get("REDIS_HOST", "localhost"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD"),
                decode_responses=True,
            )
        return self._redisClient

    def createAdmin(self, email: str, name: str, password: str) -> dict:
        try:
            normalizedEmail = str(
                TypeAdapter(EmailStr).validate_python(str(email).strip())
            ).lower()
        except ValidationError:
            normalizedEmail = ""
        normalizedName = name.strip() if isinstance(name, str) else ""
        passwordValue = password if isinstance(password, str) else ""
        errors = {}
        if not normalizedEmail:
            errors["email"] = "Invalid email format"
        if not normalizedName:
            errors["name"] = "Name is required"
        if not 12 <= len(passwordValue) <= 128:
            errors["password"] = "Password must be between 12 and 128 characters"
        if errors:
            raise AdminApiError(422, "Validation failed", errors)

        duplicate = (
            self.client.table("admin_users")
            .select("id")
            .eq("email", normalizedEmail)
            .limit(1)
            .execute().data
        )
        if duplicate:
            raise AdminApiError(409, "An administrator with this email already exists")

        now = self.nowProvider().isoformat()
        try:
            result = self.client.table("admin_users").insert({
                "email": normalizedEmail,
                "name": normalizedName,
                "password_hash": self.passwordHasher.hash(passwordValue),
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }).execute().data
        except Exception as exc:
            if _isUniqueViolation(exc):
                raise AdminApiError(409, "An administrator with this email already exists") from exc
            logger.error("Admin provisioning insert failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to create administrator") from exc

        if not result:
            raise AdminApiError(500, "Failed to create administrator")
        row = result[0]
        return {"id": row["id"], "email": row["email"], "name": row["name"]}

    def login(self, email: str, password: str, clientIp: str) -> dict:
        normalizedEmail = _normalizeEmail(email)
        rateEmail = normalizedEmail or str(email).strip().lower()
        rateIp = str(clientIp).strip()
        emailRateKey = f"admin:login:email:{_rateIdentity(rateEmail)}"
        ipRateKey = f"admin:login:ip:{_rateIdentity(rateIp)}"
        self._requireBelowThrottleLimit(emailRateKey, ADMIN_EMAIL_FAILURE_LIMIT)
        self._requireBelowThrottleLimit(ipRateKey, ADMIN_IP_FAILURE_LIMIT)

        secret = self._adminJwtSecret()
        try:
            adminRows = (
                self.client.table("admin_users")
                .select("id,email,name,password_hash,is_active")
                .eq("email", normalizedEmail)
                .limit(1)
                .execute().data
            ) if normalizedEmail else []
        except Exception as exc:
            logger.error("Admin login lookup failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Admin authentication is unavailable") from exc

        admin = adminRows[0] if adminRows else None
        storedHash = admin.get("password_hash") if admin else self._dummyPasswordHash
        try:
            passwordValid, replacementHash = self.passwordHasher.verify_and_update(
                password, storedHash
            )
        except Exception as exc:
            logger.error("Admin password verification failed: {}", type(exc).__name__)
            passwordValid, replacementHash = False, None

        if not admin or not admin.get("is_active") or not passwordValid:
            self._recordLoginFailure(emailRateKey)
            self._recordLoginFailure(ipRateKey)
            raise AdminApiError(401, "Invalid credentials")

        now = _asUtc(self.nowProvider())
        expiresAt = now + timedelta(hours=ADMIN_TOKEN_HOURS)
        sessionId = str(uuid.uuid4())
        token = jwt.encode({
            "type": "admin",
            "sub": str(admin["id"]),
            "jti": sessionId,
            "iss": ADMIN_TOKEN_ISSUER,
            "aud": ADMIN_TOKEN_AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int(expiresAt.timestamp()),
        }, secret, algorithm="HS256")

        sessionRow = {
            "id": sessionId,
            "admin_id": admin["id"],
            "token_hash": _tokenDigest(token),
            "created_at": now.isoformat(),
            "expires_at": expiresAt.isoformat(),
            "revoked_at": None,
            "last_used_at": now.isoformat(),
        }
        adminUpdate = {
            "last_login_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        if replacementHash is not None:
            adminUpdate["password_hash"] = replacementHash
        try:
            self.client.table("admin_sessions").insert(sessionRow).execute()
            self.client.table("admin_users").update(adminUpdate).eq(
                "id", admin["id"]
            ).execute()
        except Exception as exc:
            logger.error("Admin login persistence failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Admin authentication is unavailable") from exc

        return {
            "token": token,
            "admin": {
                "id": admin["id"],
                "email": admin["email"],
                "name": admin["name"],
            },
        }

    def verifyToken(self, token: str, allowRevoked: bool = False,
                    requireActiveAdmin: bool = True) -> AdminContext:
        secret = self._adminJwtSecret()
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=ADMIN_TOKEN_AUDIENCE,
                issuer=ADMIN_TOKEN_ISSUER,
            )
            if payload.get("type") != "admin":
                raise ValueError("wrong token type")
            adminId = str(uuid.UUID(str(payload["sub"])))
            sessionId = str(uuid.UUID(str(payload["jti"])))
        except (JWTError, KeyError, TypeError, ValueError) as exc:
            raise AdminApiError(401, "Invalid or expired admin session") from exc

        try:
            sessionRows = (
                self.client.table("admin_sessions")
                .select("*")
                .eq("id", sessionId)
                .limit(1)
                .execute().data
            )
            adminRows = (
                self.client.table("admin_users")
                .select("id,email,name,is_active")
                .eq("id", adminId)
                .limit(1)
                .execute().data
            )
        except Exception as exc:
            logger.error("Admin session lookup failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Admin authentication is unavailable") from exc

        if not sessionRows or not adminRows:
            raise AdminApiError(401, "Invalid or expired admin session")
        session = sessionRows[0]
        admin = adminRows[0]
        now = _asUtc(self.nowProvider())
        try:
            expiresAt = _parseTimestamp(session["expires_at"])
            lastUsedAt = _parseTimestamp(session["last_used_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdminApiError(401, "Invalid or expired admin session") from exc

        digestMatches = hmac.compare_digest(
            str(session.get("token_hash", "")), _tokenDigest(token)
        )
        revoked = session.get("revoked_at") is not None
        if (
            str(session.get("admin_id")) != adminId
            or not digestMatches
            or now >= expiresAt
            or (revoked and not allowRevoked)
            or (requireActiveAdmin and not admin.get("is_active"))
        ):
            raise AdminApiError(401, "Invalid or expired admin session")

        if not revoked and now - lastUsedAt >= ADMIN_LAST_USED_WRITE_INTERVAL:
            try:
                self.client.table("admin_sessions").update({
                    "last_used_at": now.isoformat()
                }).eq("id", sessionId).execute()
            except Exception as exc:
                logger.error("Admin session activity update failed: {}", type(exc).__name__)

        return AdminContext(
            adminId=adminId,
            email=admin["email"],
            name=admin["name"],
            sessionId=sessionId,
            token=token,
        )

    def logout(self, context: AdminContext) -> dict:
        try:
            sessionRows = (
                self.client.table("admin_sessions")
                .select("id,revoked_at")
                .eq("id", context.sessionId)
                .limit(1)
                .execute().data
            )
            if sessionRows and sessionRows[0].get("revoked_at") is None:
                self.client.table("admin_sessions").update({
                    "revoked_at": _asUtc(self.nowProvider()).isoformat()
                }).eq("id", context.sessionId).execute()
        except Exception as exc:
            logger.error("Admin logout persistence failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Admin authentication is unavailable") from exc
        return {"success": True}

    def _adminJwtSecret(self) -> str:
        secret = os.environ.get("ADMIN_JWT_SECRET")
        if not secret:
            logger.error("ADMIN_JWT_SECRET is not configured")
            raise AdminApiError(500, "Admin authentication is unavailable")
        return secret

    def _requireBelowThrottleLimit(self, key: str, limit: int) -> None:
        try:
            count = int(self.redisClient.get(key) or 0)
        except Exception as exc:
            logger.warning("Admin login throttle read failed: {}", type(exc).__name__)
            return
        if count >= limit:
            raise AdminApiError(429, "Too many login attempts. Try again later")

    def _recordLoginFailure(self, key: str) -> None:
        try:
            count = self.redisClient.incr(key)
            if count == 1:
                self.redisClient.expire(key, ADMIN_THROTTLE_WINDOW_SECONDS)
        except Exception as exc:
            logger.warning("Admin login throttle write failed: {}", type(exc).__name__)


def _isUniqueViolation(exception: Exception) -> bool:
    return "23505" in str(exception) or getattr(exception, "code", None) == "23505"


def _normalizeEmail(email: str) -> str:
    try:
        return str(
            TypeAdapter(EmailStr).validate_python(str(email).strip())
        ).lower()
    except ValidationError:
        return ""


def _asUtc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parseTimestamp(value: str) -> datetime:
    return _asUtc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def resolveAdminClientIp(request: Request) -> str:
    peerIp = request.client.host if request.client else ""
    trustedProxies = {
        value.strip()
        for value in os.environ.get("ADMIN_TRUSTED_PROXY_IPS", "").split(",")
        if value.strip()
    }
    if peerIp not in trustedProxies:
        return peerIp
    for candidate in request.headers.get("X-Forwarded-For", "").split(","):
        try:
            return str(ipaddress.ip_address(candidate.strip()))
        except ValueError:
            continue
    return peerIp


_adminAuthService: AdminAuthService | None = None


def getAdminAuthService() -> AdminAuthService:
    global _adminAuthService
    if _adminAuthService is None:
        _adminAuthService = AdminAuthService()
    return _adminAuthService


adminSecurity = HTTPBearer(auto_error=False)


def _credentialToken(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AdminApiError(401, "Invalid or expired admin session")
    return credentials.credentials


def verifyAdmin(
    credentials: HTTPAuthorizationCredentials | None = Depends(adminSecurity),
    service: AdminAuthService = Depends(getAdminAuthService),
) -> AdminContext:
    return service.verifyToken(_credentialToken(credentials))


def verifyAdminForLogout(
    credentials: HTTPAuthorizationCredentials | None = Depends(adminSecurity),
    service: AdminAuthService = Depends(getAdminAuthService),
) -> AdminContext:
    return service.verifyToken(
        _credentialToken(credentials),
        allowRevoked=True,
        requireActiveAdmin=False,
    )
