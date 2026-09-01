"""Administrator single-user free-trial extension orchestration."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from loguru import logger

from api.adminErrors import AdminApiError
from api.adminModels import AdminFreeTrialExtensionRequest
from api.services.adminAuthService import AdminContext


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _defaultEnqueue(extensionId: str) -> None:
    from nubrix.triggers.celery import syncAdminTrialCredits

    syncAdminTrialCredits.delay(extensionId)


class AdminTrialExtensionService:
    def __init__(
        self,
        repository=None,
        creditService=None,
        auditService=None,
        enqueue=None,
        nowProvider: Callable[[], datetime] | None = None,
    ):
        self._repository = repository
        self._creditService = creditService
        self._auditService = auditService
        self.enqueue = enqueue or _defaultEnqueue
        self.nowProvider = nowProvider or (lambda: datetime.now(timezone.utc))

    @property
    def repository(self):
        if self._repository is None:
            from api.services.adminTrialExtensionRepository import (
                getAdminTrialExtensionRepository,
            )

            self._repository = getAdminTrialExtensionRepository()
        return self._repository

    @property
    def creditService(self):
        if self._creditService is None:
            from api.services.credits.creditService import creditService

            self._creditService = creditService
        return self._creditService

    @property
    def auditService(self):
        if self._auditService is None:
            from api.services.adminAuditService import getAdminAuditService

            self._auditService = getAdminAuditService()
        return self._auditService

    @staticmethod
    def requestHash(payload: AdminFreeTrialExtensionRequest) -> str:
        canonical = json.dumps(
            {
                "days": payload.days,
                "reason": payload.reason,
                "userId": payload.userId,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def extend(
        self,
        payload: AdminFreeTrialExtensionRequest,
        idempotencyKey: str,
        admin: AdminContext,
    ) -> dict:
        try:
            normalizedKey = str(uuid.UUID(str(idempotencyKey)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AdminApiError(422, "Invalid Idempotency-Key header") from exc

        requestHash = self.requestHash(payload)
        try:
            extension = self.repository.createOrGetExtension(
                idempotencyKey=normalizedKey,
                requestHash=requestHash,
                userId=payload.userId,
                days=payload.days,
                reason=payload.reason,
                adminId=admin.adminId,
            )
        except AdminApiError:
            raise
        except Exception as exc:
            logger.error(
                "Admin trial-extension persistence failed: {}",
                type(exc).__name__,
            )
            raise AdminApiError(500, "Failed to extend free trials") from exc

        if str(extension.get("request_hash") or "") != requestHash:
            raise AdminApiError(409, "Idempotency key is already in use")

        extensionId = str(extension["id"])
        if extension.get("outcome") == "PENDING":
            try:
                extension = self.repository.extendUser(
                    extensionId=extensionId,
                    userId=payload.userId,
                    days=payload.days,
                    now=self.nowProvider(),
                )
            except Exception as exc:
                logger.error(
                    "Admin trial extension failed for userId={}: {}",
                    payload.userId,
                    type(exc).__name__,
                )
                try:
                    extension = self.repository.recordFailure(
                        extensionId=extensionId,
                        errorCode="EXTENSION_FAILED",
                    )
                except Exception as recordExc:
                    logger.error(
                        "Admin trial-extension failure persistence failed for "
                        "userId={}: {}",
                        payload.userId,
                        type(recordExc).__name__,
                    )
                    extension = {
                        "id": extensionId,
                        "user_id": payload.userId,
                        "outcome": "FAILED",
                        "days_added": None,
                        "previous_expiry": None,
                        "new_expiry": None,
                        "credit_sync_status": "NOT_APPLICABLE",
                        "access_still_banned": False,
                        "error_code": "EXTENSION_FAILED",
                    }

        if (
            extension.get("outcome") == "EXTENDED"
            and extension.get("credit_sync_status") == "PENDING"
        ):
            extension = self._syncCredits(extension)

        self._auditExtension(extension, admin)
        return self._publicExtension(extension)

    def _syncCredits(self, extension: dict) -> dict:
        try:
            updated = self.repository.synchronizeCreditExtension(
                str(extension["id"]), self._publishCreditExtension
            )
            if updated is not None:
                extension = updated
        except Exception as exc:
            logger.warning(
                "Admin trial credit sync failed for extension={}: {}",
                extension.get("id"),
                type(exc).__name__,
            )
        if extension.get("credit_sync_status") == "PENDING":
            try:
                self.enqueue(str(extension["id"]))
            except Exception as exc:
                logger.warning(
                    "Admin trial credit-sync enqueue failed for extension={}: {}",
                    extension.get("id"),
                    type(exc).__name__,
                )
        return extension

    def _publishCreditExtension(self, extension: dict) -> str:
        return self.creditService.refreshTrialCreditsCache(
            userId=extension["user_id"],
            quota=int(extension["credit_quota"]),
            topupTokens=int(extension.get("credit_topup_tokens") or 0),
            periodEnd=extension["credit_period_end"],
            generation=int(extension["credit_generation"]),
        )

    def _auditExtension(self, extension: dict, admin: AdminContext) -> None:
        self.auditService.record(
            action="free_trial.extend",
            targetType="user",
            targetId=extension.get("user_id"),
            changedFields=(
                ["current_period_end", "credit_balances"]
                if extension.get("outcome") == "EXTENDED"
                else []
            ),
            outcome=(
                "success"
                if extension.get("outcome") == "EXTENDED"
                else "failed"
            ),
            admin=admin,
            details={
                "extensionId": str(extension["id"]),
                "daysAdded": extension.get("days_added"),
                "errorCode": extension.get("error_code"),
                "creditSyncStatus": extension.get("credit_sync_status"),
            },
        )

    @staticmethod
    def _publicExtension(extension: dict) -> dict:
        return {
            "extensionId": str(extension["id"]),
            "userId": str(extension["user_id"]),
            "outcome": extension["outcome"],
            "daysAdded": extension.get("days_added"),
            "previousExpiry": _iso(extension.get("previous_expiry")),
            "newExpiry": _iso(extension.get("new_expiry")),
            "creditsRefreshed": (
                extension.get("outcome") == "EXTENDED"
            ),
            "creditSyncStatus": extension["credit_sync_status"],
            "accessStillBanned": bool(
                extension.get("access_still_banned")
            ),
            "errorCode": extension.get("error_code"),
        }


_adminTrialExtensionService: AdminTrialExtensionService | None = None


def getAdminTrialExtensionService() -> AdminTrialExtensionService:
    global _adminTrialExtensionService
    if _adminTrialExtensionService is None:
        _adminTrialExtensionService = AdminTrialExtensionService()
    return _adminTrialExtensionService
