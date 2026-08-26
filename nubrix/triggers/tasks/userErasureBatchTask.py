"""Reconcile durable administrator user-erasure batch ledgers."""

__all__ = ["UserErasureBatchTask"]


from loguru import logger


class UserErasureBatchTask:
    def __init__(self, service=None):
        self._service = service

    @property
    def service(self):
        if self._service is None:
            from api.services.userErasureBatchService import (
                getUserErasureBatchService,
            )

            self._service = getUserErasureBatchService()
        return self._service

    def reconcile(self, batchId: str) -> dict:
        result = self.service.reconcile(str(batchId))
        return {
            "batchId": str(result["batchId"]),
            "status": str(result["status"]),
        }

    def sweep(self, limit: int = 100) -> dict:
        batchIds = self.service.listReconcilable(limit)
        reconciled = 0
        failed = 0
        for batchId in batchIds:
            try:
                self.reconcile(str(batchId))
                reconciled += 1
            except Exception:
                failed += 1
                logger.error(
                    "User erasure batch reconciliation failed: batch={}, code={}",
                    str(batchId),
                    "BATCH_RECONCILE_FAILED",
                )
        return {
            "examined": len(batchIds),
            "reconciled": reconciled,
            "failed": failed,
        }
