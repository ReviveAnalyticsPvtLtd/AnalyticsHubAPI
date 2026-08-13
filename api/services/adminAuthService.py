from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from loguru import logger
from pwdlib import PasswordHash
from pydantic import EmailStr, TypeAdapter, ValidationError

from api.adminErrors import AdminApiError


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

    @property
    def client(self):
        if self._client is None:
            from api.commons import client
            self._client = client
        return self._client

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


def _isUniqueViolation(exception: Exception) -> bool:
    return "23505" in str(exception) or getattr(exception, "code", None) == "23505"


_adminAuthService: AdminAuthService | None = None


def getAdminAuthService() -> AdminAuthService:
    global _adminAuthService
    if _adminAuthService is None:
        _adminAuthService = AdminAuthService()
    return _adminAuthService
