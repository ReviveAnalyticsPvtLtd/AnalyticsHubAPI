"""Retry Redis publication for committed administrator trial-credit resets."""

from utils.logger import logger


class AdminTrialCreditSyncTask:
    def __init__(self, repository=None, creditService=None):
        self._repository = repository
        self._creditService = creditService

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

    def execute(self, extensionId: str) -> dict:
        extension = self.repository.getExtensionById(extensionId)
        if extension is None:
            return {"extensionId": str(extensionId), "status": "NOT_FOUND"}
        if (
            extension.get("outcome") != "EXTENDED"
            or extension.get("credit_sync_status") != "PENDING"
        ):
            return {
                "extensionId": str(extensionId),
                "status": extension.get(
                    "credit_sync_status", "NOT_APPLICABLE"
                ),
            }

        updated = self.repository.synchronizeCreditExtension(
            str(extensionId), self._publish
        )
        if updated is None:
            return {"extensionId": str(extensionId), "status": "NOT_FOUND"}
        status = updated.get("credit_sync_status", "PENDING")
        if status == "PENDING":
            return {"extensionId": str(extensionId), "status": "PENDING"}
        return {"extensionId": str(extensionId), "status": status}

    def _publish(self, extension: dict) -> str:
        return self.creditService.refreshTrialCreditsCache(
            userId=extension["user_id"],
            quota=int(extension["credit_quota"]),
            topupTokens=int(extension.get("credit_topup_tokens") or 0),
            periodEnd=extension["credit_period_end"],
            generation=int(extension["credit_generation"]),
        )

    def sweep(self, limit: int = 100) -> dict:
        extensions = self.repository.listPendingCreditSync(limit=limit)
        synced = 0
        pending = 0
        for extension in extensions:
            try:
                result = self.execute(str(extension["id"]))
                if result["status"] == "SYNCED":
                    synced += 1
                elif result["status"] == "PENDING":
                    pending += 1
            except Exception as exc:
                pending += 1
                logger.warning(
                    "Admin trial credit sync failed for extension={}: {}",
                    extension.get("id"),
                    type(exc).__name__,
                )
        return {
            "checked": len(extensions),
            "synced": synced,
            "pending": pending,
        }
