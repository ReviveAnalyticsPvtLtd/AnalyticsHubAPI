"""Administrator batch free-trial extension orchestration."""

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


def _defaultEnqueue(itemId: str) -> None:
    from nubrix.triggers.celery import syncAdminTrialCredits

    syncAdminTrialCredits.delay(itemId)


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
                "userIds": sorted(payload.userIds),
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
            batch = self.repository.createOrGetBatch(
                idempotencyKey=normalizedKey,
                requestHash=requestHash,
                days=payload.days,
                reason=payload.reason,
                adminId=admin.adminId,
                userIds=payload.userIds,
            )
        except AdminApiError:
            raise
        except Exception as exc:
            logger.error(
                "Admin trial-extension batch persistence failed: {}",
                type(exc).__name__,
            )
            raise AdminApiError(500, "Failed to extend free trials") from exc

        if str(batch.get("request_hash") or "") != requestHash:
            raise AdminApiError(409, "Idempotency key is already in use")

        batchId = str(batch["id"])
        results = []
        for userId in payload.userIds:
            item = self.repository.getItem(batchId, userId)
            if item is None:
                try:
                    item = self.repository.extendUser(
                        batchId=batchId,
                        userId=userId,
                        days=payload.days,
                        now=self.nowProvider(),
                    )
                except Exception as exc:
                    logger.error(
                        "Admin trial extension failed for userId={}: {}",
                        userId,
                        type(exc).__name__,
                    )
                    try:
                        item = self.repository.recordFailure(
                            batchId=batchId,
                            userId=userId,
                            errorCode="EXTENSION_FAILED",
                        )
                    except Exception as recordExc:
                        logger.error(
                            "Admin trial-extension failure persistence failed for "
                            "userId={}: {}",
                            userId,
                            type(recordExc).__name__,
                        )
                        item = {
                            "id": None,
                            "user_id": userId,
                            "outcome": "FAILED",
                            "days_added": None,
                            "previous_expiry": None,
                            "new_expiry": None,
                            "credit_sync_status": "NOT_APPLICABLE",
                            "access_still_banned": False,
                            "error_code": "EXTENSION_FAILED",
                        }

            if (
                item.get("outcome") == "EXTENDED"
                and item.get("credit_sync_status") == "PENDING"
            ):
                item = self._syncCredits(item)

            self._auditItem(item, batchId, admin)
            results.append(self._publicItem(item))

        try:
            self.repository.completeBatch(batchId)
        except Exception as exc:
            logger.warning(
                "Admin trial-extension batch completion marker failed: {}",
                type(exc).__name__,
            )

        extended = sum(result["outcome"] == "EXTENDED" for result in results)
        failed = len(results) - extended
        pending = sum(
            result["creditSyncStatus"] == "PENDING" for result in results
        )
        return {
            "batchId": batchId,
            "status": "PARTIAL_SUCCESS" if failed else "COMPLETED",
            "days": payload.days,
            "summary": {
                "requested": len(results),
                "extended": extended,
                "failed": failed,
                "creditSyncPending": pending,
            },
            "results": results,
        }

    def _syncCredits(self, item: dict) -> dict:
        try:
            updated = self.repository.synchronizeCreditItem(
                str(item["id"]), self._publishCreditItem
            )
            if updated is not None:
                item = updated
        except Exception as exc:
            logger.warning(
                "Admin trial credit sync failed for item={}: {}",
                item.get("id"),
                type(exc).__name__,
            )
        if item.get("credit_sync_status") == "PENDING":
            try:
                self.enqueue(str(item["id"]))
            except Exception as exc:
                logger.warning(
                    "Admin trial credit-sync enqueue failed for item={}: {}",
                    item.get("id"),
                    type(exc).__name__,
                )
        return item

    def _publishCreditItem(self, item: dict) -> str:
        return self.creditService.refreshTrialCreditsCache(
            userId=item["user_id"],
            quota=int(item["credit_quota"]),
            topupTokens=int(item.get("credit_topup_tokens") or 0),
            periodEnd=item["credit_period_end"],
            generation=int(item["credit_generation"]),
        )

    def _auditItem(self, item: dict, batchId: str, admin: AdminContext) -> None:
        self.auditService.record(
            action="free_trial.extend",
            targetType="user",
            targetId=item.get("user_id"),
            changedFields=(
                ["current_period_end", "credit_balances"]
                if item.get("outcome") == "EXTENDED"
                else []
            ),
            outcome=(
                "success" if item.get("outcome") == "EXTENDED" else "failed"
            ),
            admin=admin,
            details={
                "batchId": batchId,
                "daysAdded": item.get("days_added"),
                "errorCode": item.get("error_code"),
                "creditSyncStatus": item.get("credit_sync_status"),
            },
        )

    @staticmethod
    def _publicItem(item: dict) -> dict:
        return {
            "userId": str(item["user_id"]),
            "outcome": item["outcome"],
            "daysAdded": item.get("days_added"),
            "previousExpiry": _iso(item.get("previous_expiry")),
            "newExpiry": _iso(item.get("new_expiry")),
            "creditsRefreshed": (
                item.get("outcome") == "EXTENDED"
            ),
            "creditSyncStatus": item["credit_sync_status"],
            "accessStillBanned": bool(item.get("access_still_banned")),
            "errorCode": item.get("error_code"),
        }


_adminTrialExtensionService: AdminTrialExtensionService | None = None


def getAdminTrialExtensionService() -> AdminTrialExtensionService:
    global _adminTrialExtensionService
    if _adminTrialExtensionService is None:
        _adminTrialExtensionService = AdminTrialExtensionService()
    return _adminTrialExtensionService
