"""Administrator-facing preview and status service for batch user erasure."""

__all__ = ["UserErasureBatchService", "getUserErasureBatchService"]


import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime

from loguru import logger

from api.adminErrors import AdminApiError
from api.adminModels import (
    AdminUserErasureBatchConfirmRequest,
    AdminUserErasureBatchPreviewRequest,
)
from api.services.adminAuthService import AdminContext


_CLASSIFICATION_SUMMARY_KEYS = {
    "READY": "ready",
    "ALREADY_IN_PROGRESS": "alreadyInProgress",
    "ALREADY_COMPLETED": "alreadyCompleted",
    "USER_NOT_FOUND": "notFound",
}


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _requestHash(userIds: list[str], reason: str | None) -> str:
    canonical = json.dumps(
        {"userIds": sorted(userIds), "reason": reason},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _subjectFingerprint(secret: str, userId: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), userId.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _defaultEnqueue(requestId: str) -> None:
    from nubrix.triggers.celery import runUserErasure

    runUserErasure.delay(requestId)


class UserErasureBatchService:
    def __init__(self, repository=None, auditService=None, enqueue=None):
        self._repository = repository
        self._auditService = auditService
        self.enqueue = enqueue or _defaultEnqueue

    @property
    def repository(self):
        if self._repository is None:
            from api.services.userErasureBatchRepository import (
                getUserErasureBatchRepository,
            )

            self._repository = getUserErasureBatchRepository()
        return self._repository

    @property
    def auditService(self):
        if self._auditService is None:
            from api.services.adminAuditService import getAdminAuditService

            self._auditService = getAdminAuditService()
        return self._auditService

    def preview(
        self,
        payload: AdminUserErasureBatchPreviewRequest,
        idempotencyKey: str,
        admin: AdminContext,
    ) -> dict:
        if os.environ.get("USER_ERASURE_ENABLED", "false").strip().lower() != "true":
            raise AdminApiError(503, "User erasure is not enabled")

        normalizedKey = self._normalizeIdempotencyKey(idempotencyKey)
        secret = self._hmacSecret()
        requestHash = _requestHash(payload.userIds, payload.reason)

        try:
            existing = self.repository.findByIdempotency(normalizedKey)
        except Exception as exc:
            logger.error(
                "User erasure batch idempotency lookup failed: {}",
                type(exc).__name__,
            )
            raise AdminApiError(500, "Failed to preview user erasure batch") from exc

        if existing is not None:
            if not hmac.compare_digest(
                str(existing.get("request_hash") or ""), requestHash
            ):
                raise AdminApiError(409, "Idempotency key is already in use")
            return self._serialize(existing)

        userItems = [
            {
                "ordinal": ordinal,
                "userId": userId,
                "subjectFingerprint": _subjectFingerprint(secret, userId),
            }
            for ordinal, userId in enumerate(payload.userIds)
        ]
        try:
            batch = self.repository.createPreview(
                userItems=userItems,
                adminId=admin.adminId,
                idempotencyKey=normalizedKey,
                requestHash=requestHash,
                reason=payload.reason,
            )
        except AdminApiError:
            raise
        except Exception as exc:
            logger.error(
                "User erasure batch preview persistence failed: {}",
                type(exc).__name__,
            )
            concurrent = self._reloadAfterCreateFailure(normalizedKey)
            if concurrent is not None:
                if not hmac.compare_digest(
                    str(concurrent.get("request_hash") or ""), requestHash
                ):
                    raise AdminApiError(
                        409, "Idempotency key is already in use"
                    ) from exc
                return self._serialize(concurrent)
            raise AdminApiError(500, "Failed to preview user erasure batch") from exc

        result = self._serialize(batch)
        self.auditService.record(
            action="user.erasure_batch.preview",
            targetType="user_erasure_batch",
            targetId=str(batch["id"]),
            changedFields=["status"],
            outcome="success",
            admin=admin,
            details=dict(result["summary"]),
        )
        return result

    def getStatus(self, batchId: str, admin: AdminContext) -> dict:
        del admin
        normalizedBatchId = self._normalizeBatchId(batchId)
        try:
            batch = self.repository.getBatch(normalizedBatchId)
        except Exception as exc:
            logger.error(
                "User erasure batch status lookup failed: {}", type(exc).__name__
            )
            raise AdminApiError(500, "Failed to read user erasure batch") from exc
        if batch is None:
            raise AdminApiError(404, "User erasure batch not found")
        return self._serialize(batch)

    def listReconcilable(self, limit: int = 100) -> list[str]:
        try:
            return self.repository.listReconcilable(limit)
        except Exception as exc:
            logger.error(
                "User erasure batch reconciliation lookup failed: {}",
                type(exc).__name__,
            )
            raise AdminApiError(
                500, "Failed to reconcile user erasure batches"
            ) from exc

    def reconcile(self, batchId: str) -> dict:
        normalizedBatchId = self._normalizeBatchId(batchId)
        try:
            batch = self.repository.reconcileBatch(normalizedBatchId)
        except Exception as exc:
            logger.error(
                "User erasure batch reconciliation failed: {}",
                type(exc).__name__,
            )
            raise AdminApiError(
                500, "Failed to reconcile user erasure batch"
            ) from exc
        if batch is None:
            raise AdminApiError(404, "User erasure batch not found")
        return self._serialize(batch)

    def confirm(
        self,
        batchId: str,
        payload: AdminUserErasureBatchConfirmRequest,
        admin: AdminContext,
    ) -> dict:
        normalizedBatchId = self._normalizeBatchId(batchId)
        if os.environ.get("USER_ERASURE_ENABLED", "false").strip().lower() != "true":
            raise AdminApiError(503, "User erasure is not enabled")

        try:
            batch = self.repository.confirmBatch(
                batchId=normalizedBatchId,
                adminId=admin.adminId,
                sessionId=admin.sessionId,
                confirmation=payload.confirmation,
            )
        except AdminApiError:
            raise
        except Exception as exc:
            logger.error(
                "User erasure batch confirmation failed: {}",
                type(exc).__name__,
            )
            raise AdminApiError(
                500, "Failed to confirm user erasure batch"
            ) from exc

        linkedRequestIds = []
        completedRequestIds = set()
        for item in batch.get("items") or []:
            requestId = item.get("request_id")
            if requestId is None:
                continue
            normalizedRequestId = str(requestId)
            if normalizedRequestId not in linkedRequestIds:
                linkedRequestIds.append(normalizedRequestId)
            if item.get("request_status") == "COMPLETED":
                completedRequestIds.add(normalizedRequestId)

        queued = 0
        queueFailed = False
        for requestId in linkedRequestIds:
            if requestId in completedRequestIds:
                continue
            try:
                self.enqueue(requestId)
                queued += 1
            except Exception as exc:
                queueFailed = True
                logger.warning(
                    "User erasure batch enqueue failed: {}",
                    type(exc).__name__,
                )

        result = self._serialize(batch)
        self.auditService.record(
            action="user.erasure_batch.confirm",
            targetType="user_erasure_batch",
            targetId=str(batch["id"]),
            changedFields=["status", "confirmed_at"],
            outcome="side_effect_failed" if queueFailed else "success",
            admin=admin,
            details={
                "requested": len(batch.get("items") or []),
                "linked": len(linkedRequestIds),
                "queued": queued,
            },
        )
        return result

    def _reloadAfterCreateFailure(self, idempotencyKey: str) -> dict | None:
        try:
            return self.repository.findByIdempotency(idempotencyKey)
        except Exception as exc:
            logger.error(
                "User erasure batch conflict reload failed: {}",
                type(exc).__name__,
            )
            return None

    @staticmethod
    def _normalizeIdempotencyKey(idempotencyKey: str) -> str:
        try:
            return str(uuid.UUID(str(idempotencyKey)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AdminApiError(422, "Invalid Idempotency-Key header") from exc

    @staticmethod
    def _normalizeBatchId(batchId: str) -> str:
        try:
            return str(uuid.UUID(str(batchId)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AdminApiError(
                422, "Invalid user erasure batch ID"
            ) from exc

    @staticmethod
    def _hmacSecret() -> str:
        secret = os.environ.get("USER_ERASURE_HMAC_SECRET")
        if not secret:
            logger.error("USER_ERASURE_HMAC_SECRET is not configured")
            raise AdminApiError(500, "User erasure is unavailable")
        return secret

    @classmethod
    def _serialize(cls, batch: dict) -> dict:
        items = list(batch.get("items") or [])
        summary = {
            "requested": len(items),
            "ready": 0,
            "alreadyInProgress": 0,
            "alreadyCompleted": 0,
            "notFound": 0,
        }
        for item in items:
            summaryKey = _CLASSIFICATION_SUMMARY_KEYS.get(item.get("classification"))
            if summaryKey is not None:
                summary[summaryKey] += 1

        status = cls._displayBatchStatus(batch, items)
        readyCount = summary["ready"]
        requiredConfirmation = None
        if status == "PREVIEWED" and readyCount:
            requiredConfirmation = (
                "ERASE 1 USER"
                if readyCount == 1
                else f"ERASE {readyCount} USERS"
            )

        return {
            "batchId": str(batch["id"]),
            "status": status,
            "expiresAt": _iso(batch.get("expires_at")),
            "requiredConfirmation": requiredConfirmation,
            "summary": summary,
            "results": [cls._serializeItem(item) for item in items],
        }

    @staticmethod
    def _serializeItem(item: dict) -> dict:
        linkedStatus = item.get("request_status")
        return {
            "itemId": str(item["id"]),
            "userId": (
                str(item["target_user_id"])
                if item.get("target_user_id") is not None
                else None
            ),
            "status": linkedStatus or item["classification"],
            "requestId": (
                str(item["request_id"])
                if item.get("request_id") is not None
                else None
            ),
            "errorCode": item.get("error_code"),
        }

    @staticmethod
    def _displayBatchStatus(batch: dict, items: list[dict]) -> str:
        storedStatus = str(batch.get("status") or "")
        if storedStatus in {"PREVIEWED", "EXPIRED"}:
            return storedStatus

        childStatuses = [
            str(item["request_status"])
            for item in items
            if item.get("request_status") is not None
        ]
        if "PARTIALLY_FAILED" in childStatuses:
            return "PARTIALLY_FAILED"
        if any(status in {"PENDING", "IN_PROGRESS"} for status in childStatuses):
            return "IN_PROGRESS"
        if childStatuses and all(status == "COMPLETED" for status in childStatuses):
            return "COMPLETED"
        return storedStatus


_userErasureBatchService: UserErasureBatchService | None = None


def getUserErasureBatchService() -> UserErasureBatchService:
    global _userErasureBatchService
    if _userErasureBatchService is None:
        _userErasureBatchService = UserErasureBatchService()
    return _userErasureBatchService
