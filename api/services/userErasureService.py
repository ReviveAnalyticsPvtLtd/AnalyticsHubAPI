"""Administrator-facing initiation and status service for user erasure."""

__all__ = ["UserErasureService", "getUserErasureService"]


import hashlib
import hmac
import os
import uuid
from datetime import datetime

from loguru import logger

from api.adminErrors import AdminApiError
from api.adminModels import AdminUserAccessPatch, AdminUserErasureRequest
from api.services.adminAuthService import AdminContext


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _defaultEnqueue(requestId: str) -> None:
    from nubrix.triggers.celery import runUserErasure

    runUserErasure.delay(requestId)


class UserErasureService:
    def __init__(
        self,
        repository=None,
        accessService=None,
        auditService=None,
        enqueue=None,
    ):
        self._repository = repository
        self._accessService = accessService
        self._auditService = auditService
        self.enqueue = enqueue or _defaultEnqueue

    @property
    def repository(self):
        if self._repository is None:
            from api.services.userErasureRepository import getUserErasureRepository

            self._repository = getUserErasureRepository()
        return self._repository

    @property
    def accessService(self):
        if self._accessService is None:
            from api.services.adminManagementService import getAdminManagementService

            self._accessService = getAdminManagementService()
        return self._accessService

    @property
    def auditService(self):
        if self._auditService is None:
            from api.services.adminAuditService import getAdminAuditService

            self._auditService = getAdminAuditService()
        return self._auditService

    def start(
        self,
        userId: str,
        payload: AdminUserErasureRequest,
        idempotencyKey: str,
        admin: AdminContext,
    ) -> dict:
        if os.environ.get("USER_ERASURE_ENABLED", "false").strip().lower() != "true":
            raise AdminApiError(503, "User erasure is not enabled")

        try:
            normalizedKey = str(uuid.UUID(str(idempotencyKey)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AdminApiError(422, "Invalid Idempotency-Key header") from exc

        fingerprint = self._subjectFingerprint(userId)
        try:
            existing = self.repository.findByIdempotency(normalizedKey)
        except Exception as exc:
            logger.error("User erasure idempotency lookup failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to start user erasure") from exc

        if existing is not None:
            if not hmac.compare_digest(
                str(existing.get("subject_fingerprint") or ""), fingerprint
            ):
                raise AdminApiError(409, "Idempotency key is already in use")
            self._enqueueBestEffort(existing["id"])
            return self._accepted(existing, userId)

        self.accessService.setUserAccess(
            userId,
            AdminUserAccessPatch(banned=True, reason=payload.reason),
            admin,
        )

        try:
            request = self.repository.createRequest(
                userId=userId,
                subjectFingerprint=fingerprint,
                adminId=admin.adminId,
                idempotencyKey=normalizedKey,
                reason=payload.reason,
            )
        except AdminApiError:
            raise
        except Exception as exc:
            logger.error("User erasure request persistence failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to start user erasure") from exc

        queued = self._enqueueBestEffort(request["id"])
        self.auditService.record(
            action="user.erasure.start",
            targetType="user_erasure_request",
            targetId=str(request["id"]),
            changedFields=["status", "erasure_pending"],
            outcome="success" if queued else "side_effect_failed",
            admin=admin,
            details={
                "targetFingerprint": fingerprint,
                "queued": queued,
            },
        )
        return self._accepted(request, userId)

    def getStatus(self, requestId: str) -> dict:
        try:
            normalizedId = str(uuid.UUID(str(requestId)))
        except (TypeError, ValueError, AttributeError):
            normalizedId = str(requestId)

        try:
            request = self.repository.getRequest(normalizedId)
        except Exception as exc:
            logger.error("User erasure status lookup failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to read erasure status") from exc
        if request is None:
            raise AdminApiError(404, "Erasure request not found")

        return {
            "requestId": str(request["id"]),
            "status": request["status"],
            "createdAt": _iso(request["created_at"]),
            "startedAt": _iso(request.get("started_at")),
            "completedAt": _iso(request.get("completed_at")),
            "lastErrorCode": request.get("last_error_code"),
            "steps": [
                {
                    "name": step["step_name"],
                    "status": step["status"],
                    "attempts": int(step.get("attempt_count") or 0),
                    "lastErrorCode": step.get("last_error_code"),
                }
                for step in request.get("steps", [])
            ],
        }

    def _subjectFingerprint(self, userId: str) -> str:
        secret = os.environ.get("USER_ERASURE_HMAC_SECRET")
        if not secret:
            logger.error("USER_ERASURE_HMAC_SECRET is not configured")
            raise AdminApiError(500, "User erasure is unavailable")
        return hmac.new(
            secret.encode("utf-8"),
            str(userId).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _enqueueBestEffort(self, requestId: str) -> bool:
        try:
            self.enqueue(str(requestId))
            return True
        except Exception as exc:
            logger.warning("User erasure enqueue failed: {}", type(exc).__name__)
            return False

    @staticmethod
    def _accepted(request: dict, userId: str) -> dict:
        return {
            "requestId": str(request["id"]),
            "userId": str(userId),
            "status": request["status"],
            "createdAt": _iso(request["created_at"]),
        }


_userErasureService: UserErasureService | None = None


def getUserErasureService() -> UserErasureService:
    global _userErasureService
    if _userErasureService is None:
        _userErasureService = UserErasureService()
    return _userErasureService
