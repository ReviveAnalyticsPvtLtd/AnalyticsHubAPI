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
ADMIN_TOKEN_SECONDS = ADMIN_TOKEN_HOURS * 60 * 60

_ADMIN_THROTTLE_ADMIT_SCRIPT = """-- admin-login-admit
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if count > tonumber(ARGV[1]) then
    redis.call('DECR', KEYS[1])
    return 0
end
return 1
"""

_ADMIN_THROTTLE_RELEASE_SCRIPT = """-- admin-login-release
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count <= 1 then
    redis.call('DEL', KEYS[1])
    return 1
end
redis.call('DECR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return 1
"""


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
        errors.update(_passwordErrors(passwordValue))
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

    def listAdmins(self) -> list[dict]:
        """
        Return every administrator account, newest registration last.

        Never selects password_hash. Operators reading this list have no reason
        to see hashes, and not selecting them keeps them out of logs and shells.
        """
        try:
            rows = (
                self.client.table("admin_users")
                .select("id,email,name,is_active,last_login_at,created_at")
                .order("created_at")
                .execute().data
            ) or []
        except Exception as exc:
            logger.error("Admin list failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to list administrators") from exc
        return [
            {
                "id": row.get("id"),
                "email": row.get("email"),
                "name": row.get("name"),
                "is_active": bool(row.get("is_active")),
                "last_login_at": row.get("last_login_at"),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]

    def setAdminActive(self, email: str, isActive: bool) -> dict:
        """
        Enable or disable an administrator account.

        Deactivation also revokes every live session. verifyToken already
        re-checks is_active on each request, so revocation is not what makes
        deactivation effective — it is what makes the reason visible to anyone
        later auditing admin_sessions.

        Args:
            email (str): Administrator email, matched case-insensitively.
            isActive (bool): Target state.

        Returns:
            dict: id, email, name, and the applied is_active value.
        """
        admin = self._requireAdminByEmail(email)
        now = _asUtc(self.nowProvider()).isoformat()
        try:
            self.client.table("admin_users").update({
                "is_active": bool(isActive),
                "updated_at": now,
            }).eq("id", admin["id"]).execute()
        except Exception as exc:
            logger.error("Admin activation update failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to update administrator") from exc

        revoked = 0
        if not isActive:
            revoked = self.revokeAllSessions(admin["id"])

        self._recordLifecycleAudit(
            "admin.activate" if isActive else "admin.deactivate",
            admin,
            ["is_active"],
        )
        return {
            "id": admin["id"],
            "email": admin["email"],
            "name": admin["name"],
            "is_active": bool(isActive),
            "revokedSessions": revoked,
        }

    def changeAdminPassword(self, email: str, newPassword: str) -> dict:
        """
        Replace an administrator password and revoke every live session.

        Session revocation is mandatory, not optional. A password reset exists
        because the old credential may be compromised; leaving the holder's
        existing eight-hour token valid would make the reset cosmetic.

        Args:
            email (str): Administrator email, matched case-insensitively.
            newPassword (str): Replacement password, 12-128 characters.

        Returns:
            dict: id, email, name, and the number of sessions revoked.
        """
        passwordValue = newPassword if isinstance(newPassword, str) else ""
        errors = _passwordErrors(passwordValue)
        if errors:
            raise AdminApiError(422, "Validation failed", errors)

        admin = self._requireAdminByEmail(email)
        now = _asUtc(self.nowProvider()).isoformat()
        try:
            self.client.table("admin_users").update({
                "password_hash": self.passwordHasher.hash(passwordValue),
                "updated_at": now,
            }).eq("id", admin["id"]).execute()
        except Exception as exc:
            logger.error("Admin password update failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to update administrator") from exc

        revoked = self.revokeAllSessions(admin["id"])
        self._recordLifecycleAudit("admin.password_reset", admin, ["password_hash"])
        return {
            "id": admin["id"],
            "email": admin["email"],
            "name": admin["name"],
            "revokedSessions": revoked,
        }

    def revokeAllSessions(self, adminId: str) -> int:
        """
        Revoke every session for one administrator that is not already revoked.

        Returns:
            int: Number of sessions revoked.
        """
        try:
            revokedRows = (
                self.client.table("admin_sessions")
                .update({"revoked_at": _asUtc(self.nowProvider()).isoformat()})
                .eq("admin_id", adminId)
                .is_("revoked_at", "null")
                .execute().data
            ) or []
        except Exception as exc:
            logger.error("Admin session revocation failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to revoke administrator sessions") from exc
        return len(revokedRows)

    def _requireAdminByEmail(self, email: str) -> dict:
        normalizedEmail = _normalizeEmail(email)
        if not normalizedEmail:
            raise AdminApiError(
                422, "Validation failed", {"email": "Invalid email format"}
            )
        try:
            rows = (
                self.client.table("admin_users")
                .select("id,email,name,is_active")
                .eq("email", normalizedEmail)
                .limit(1)
                .execute().data
            ) or []
        except Exception as exc:
            logger.error("Admin lookup failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to load administrator") from exc
        if not rows:
            raise AdminApiError(404, "Administrator not found")
        return rows[0]

    @staticmethod
    def _recordLifecycleAudit(action: str, admin: dict, changedFields: list[str]) -> None:
        """
        Record a CLI lifecycle action, tolerating an unavailable audit backend.

        Import is deferred because adminAuditService imports AdminContext from
        this module; importing at module scope would be circular.
        """
        try:
            from api.services.adminAuditService import getAdminAuditService

            getAdminAuditService().record(
                action=action,
                targetType="admin",
                targetId=str(admin.get("id")),
                changedFields=changedFields,
                outcome="success",
                actorEmail=os.environ.get("ADMIN_CLI_ACTOR") or "cli",
            )
        except Exception as exc:
            logger.error(
                "Durable admin audit unavailable for {}: {}",
                action,
                type(exc).__name__,
            )

    def login(self, email: str, password: str, clientIp: str) -> dict:
        normalizedEmail = _normalizeEmail(email)
        rateEmail = normalizedEmail or str(email).strip().lower()
        rateIp = str(clientIp).strip()
        emailRateKey = f"admin:login:email:{_rateIdentity(rateEmail)}"
        ipRateKey = f"admin:login:ip:{_rateIdentity(rateIp)}"
        emailReserved = self._admitLoginAttempt(
            emailRateKey, ADMIN_EMAIL_FAILURE_LIMIT
        )
        try:
            ipReserved = self._admitLoginAttempt(ipRateKey, ADMIN_IP_FAILURE_LIMIT)
        except AdminApiError:
            if emailReserved:
                self._releaseLoginAttempt(emailRateKey)
            raise

        try:
            response = self._authenticateAndPersist(normalizedEmail, password)
        except AdminApiError as exc:
            if exc.statusCode != 401:
                if emailReserved:
                    self._releaseLoginAttempt(emailRateKey)
                if ipReserved:
                    self._releaseLoginAttempt(ipRateKey)
            raise
        except Exception:
            if emailReserved:
                self._releaseLoginAttempt(emailRateKey)
            if ipReserved:
                self._releaseLoginAttempt(ipRateKey)
            raise

        self._clearLoginFailures(emailRateKey)
        if ipReserved:
            self._releaseLoginAttempt(ipRateKey)
        return response

    def _authenticateAndPersist(self, normalizedEmail: str, password: str) -> dict:
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
                options={
                    "require_sub": True,
                    "require_jti": True,
                    "require_type": True,
                    "require_iss": True,
                    "require_aud": True,
                    "require_iat": True,
                    "require_exp": True,
                },
            )
            if (
                type(payload["sub"]) is not str
                or type(payload["jti"]) is not str
                or type(payload["type"]) is not str
                or type(payload["iss"]) is not str
                or type(payload["aud"]) is not str
                or type(payload["iat"]) is not int
                or type(payload["exp"]) is not int
                or payload["type"] != "admin"
                or payload["iss"] != ADMIN_TOKEN_ISSUER
                or payload["aud"] != ADMIN_TOKEN_AUDIENCE
                or payload["exp"] - payload["iat"] != ADMIN_TOKEN_SECONDS
            ):
                raise ValueError("invalid admin claims")
            adminId = str(uuid.UUID(payload["sub"]))
            sessionId = str(uuid.UUID(payload["jti"]))
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
            observedLastUsedAt = session["last_used_at"]
            lastUsedAt = _parseTimestamp(observedLastUsedAt)
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
                }).eq("id", sessionId).eq(
                    "last_used_at", observedLastUsedAt
                ).execute()
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
        secret = os.environ.get("SECRET_KEY")
        if secret:
            return secret
        logger.error("SECRET_KEY is not configured")
        raise AdminApiError(500, "Admin authentication is unavailable")

    def _admitLoginAttempt(self, key: str, limit: int) -> bool:
        try:
            admitted = self.redisClient.eval(
                _ADMIN_THROTTLE_ADMIT_SCRIPT,
                1,
                key,
                limit,
                ADMIN_THROTTLE_WINDOW_SECONDS,
            )
        except Exception as exc:
            logger.warning("Admin login throttle admission failed: {}", type(exc).__name__)
            return False
        if int(admitted) != 1:
            raise AdminApiError(429, "Too many login attempts. Try again later")
        return True

    def _releaseLoginAttempt(self, key: str) -> None:
        try:
            self.redisClient.eval(
                _ADMIN_THROTTLE_RELEASE_SCRIPT,
                1,
                key,
                ADMIN_THROTTLE_WINDOW_SECONDS,
            )
        except Exception as exc:
            logger.warning("Admin login throttle release failed: {}", type(exc).__name__)

    def _clearLoginFailures(self, key: str) -> None:
        try:
            self.redisClient.delete(key)
        except Exception as exc:
            logger.warning("Admin login throttle cleanup failed: {}", type(exc).__name__)


def _passwordErrors(password: str) -> dict[str, str]:
    """
    Single source of truth for the admin password rule.

    Both createAdmin and changeAdminPassword call this so the accepted length
    cannot drift between provisioning and rotation.
    """
    if not 12 <= len(password) <= 128:
        return {"password": "Password must be between 12 and 128 characters"}
    return {}


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
