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

    def execute(self, itemId: str) -> dict:
        item = self.repository.getItemById(itemId)
        if item is None:
            return {"itemId": str(itemId), "status": "NOT_FOUND"}
        if (
            item.get("outcome") != "EXTENDED"
            or item.get("credit_sync_status") != "PENDING"
        ):
            return {
                "itemId": str(itemId),
                "status": item.get("credit_sync_status", "NOT_APPLICABLE"),
            }

        updated = self.repository.synchronizeCreditItem(
            str(itemId), self._publish
        )
        if updated is None:
            return {"itemId": str(itemId), "status": "NOT_FOUND"}
        status = updated.get("credit_sync_status", "PENDING")
        if status == "PENDING":
            return {"itemId": str(itemId), "status": "PENDING"}
        return {"itemId": str(itemId), "status": status}

    def _publish(self, item: dict) -> str:
        return self.creditService.refreshTrialCreditsCache(
            userId=item["user_id"],
            quota=int(item["credit_quota"]),
            topupTokens=int(item.get("credit_topup_tokens") or 0),
            periodEnd=item["credit_period_end"],
            generation=int(item["credit_generation"]),
        )

    def sweep(self, limit: int = 100) -> dict:
        items = self.repository.listPendingCreditSync(limit=limit)
        synced = 0
        pending = 0
        for item in items:
            try:
                result = self.execute(str(item["id"]))
                if result["status"] == "SYNCED":
                    synced += 1
                elif result["status"] == "PENDING":
                    pending += 1
            except Exception as exc:
                pending += 1
                logger.warning(
                    "Admin trial credit sync failed for item={}: {}",
                    item.get("id"),
                    type(exc).__name__,
                )
        return {"checked": len(items), "synced": synced, "pending": pending}
