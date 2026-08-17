"""
adminSessionCleanupTask.py

Retention sweep for the admin auth tables.

Runs daily via Celery Beat. Admin tokens live eight hours, so `admin_sessions`
rows become dead weight quickly; nothing else deletes them. Audit records are
kept far longer because they are the durable record of who changed what.

Both tables are swept independently. A failure on one is logged and does not
prevent the other from running, because the failure mode this guards against is
a table quietly growing for months while its neighbour succeeds.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "AdminSessionCleanupTask",
    "SESSION_RETENTION_DAYS",
    "AUDIT_RETENTION_DAYS",
]


from datetime import datetime, timedelta, timezone

from api.commons import client as supabaseClient
from utils.logger import logger


SESSION_RETENTION_DAYS = 30
AUDIT_RETENTION_DAYS = 365
ADMIN_SESSIONS_TABLE = "admin_sessions"
ADMIN_AUDIT_TABLE = "admin_audit_log"


def utcNow() -> datetime:
    return datetime.now(timezone.utc)


class AdminSessionCleanupTask:
    """
    Deletes expired admin sessions and aged admin audit records.
    """

    def __init__(self, client=None, now=None):
        self.client = client if client is not None else supabaseClient
        self._now = now or utcNow

    def execute(self) -> dict:
        logger.info("Admin session cleanup task started")
        currentTime = self._now()

        sessionsDeleted = self._sweep(
            ADMIN_SESSIONS_TABLE,
            "expires_at",
            (currentTime - timedelta(days=SESSION_RETENTION_DAYS)).isoformat(),
        )
        auditRowsDeleted = self._sweep(
            ADMIN_AUDIT_TABLE,
            "created_at",
            (currentTime - timedelta(days=AUDIT_RETENTION_DAYS)).isoformat(),
        )

        result = {
            "sessionsDeleted": sessionsDeleted,
            "auditRowsDeleted": auditRowsDeleted,
        }
        logger.info(f"Admin session cleanup task finished: {result}")
        return result

    def _sweep(self, tableName: str, column: str, cutoff: str) -> int:
        """
        Delete rows in one table older than the cutoff.

        Args:
            tableName (str): Table to sweep.
            column (str): Timestamp column compared against the cutoff.
            cutoff (str): ISO-8601 timestamp; rows strictly older are removed.

        Returns:
            int: Number of rows deleted, or 0 when the sweep failed.
        """
        try:
            deleted = (
                self.client.table(tableName)
                .delete()
                .lt(column, cutoff)
                .execute().data
            ) or []
        except Exception as exc:
            logger.warning(
                f"Admin cleanup could not sweep {tableName}: {type(exc).__name__}"
            )
            return 0
        return len(deleted)
