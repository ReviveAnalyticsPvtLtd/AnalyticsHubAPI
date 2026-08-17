"""
adminAuditService.py

Durable audit trail for administrator actions.

Every admin mutation is dual-written: a structured `admin_audit` line through
Loguru (which reaches Logtail) and a row in `public.admin_audit_log`. Neither
sink is sufficient alone. The log stream is lost if Logtail is misconfigured
and is subject to its retention limits; the durable row cannot be written when
Supabase is itself the reason the audited action failed. Writing both means a
record survives either failure.

Recording never raises. An audit write must never be the reason an admin
action fails.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "ADMIN_AUDIT_TABLE",
    "ADMIN_AUDIT_MAX_PAGE_SIZE",
    "AdminAuditService",
    "getAdminAuditService",
]


import json
from datetime import datetime, timezone
from typing import Callable

from loguru import logger

from api.adminErrors import AdminApiError
from api.services.adminAuthService import AdminContext


ADMIN_AUDIT_TABLE = "admin_audit_log"
ADMIN_AUDIT_MAX_PAGE_SIZE = 200
ADMIN_AUDIT_DEFAULT_PAGE_SIZE = 50
ADMIN_AUDIT_SYSTEM_ACTOR = "system"
ADMIN_AUDIT_FIELDS = (
    "id", "admin_id", "admin_email", "session_id", "actor_type", "action",
    "target_type", "target_id", "changed_fields", "outcome", "created_at",
)
ADMIN_AUDIT_SELECT = ",".join(ADMIN_AUDIT_FIELDS)


class AdminAuditService:
    def __init__(self, client=None, nowProvider: Callable[[], datetime] | None = None):
        self._client = client
        self.nowProvider = nowProvider or (lambda: datetime.now(timezone.utc))

    @property
    def client(self):
        if self._client is None:
            from api.commons import client
            self._client = client
        return self._client

    def record(
        self,
        action: str,
        targetType: str,
        targetId: str | None,
        changedFields: list[str] | None,
        outcome: str,
        admin: AdminContext | None = None,
        actorEmail: str | None = None,
        emitLog: bool = True,
    ) -> None:
        """
        Write one audit record to both the log stream and the durable table.

        Never raises. A durable-write failure is logged and swallowed so the
        caller's own outcome is unchanged.

        Args:
            action (str): Dotted action name, e.g. "user.update".
            targetType (str): "user", "subscription", or "admin".
            targetId (str | None): Identifier of the affected row.
            changedFields (list[str] | None): Field names touched, if any.
            outcome (str): "success", "conflict", "not_found", "invalid",
                "failed", "side_effect_failed", or "compensation_failed".
            admin (AdminContext | None): Present for HTTP-authenticated actions.
            actorEmail (str | None): Identity for CLI actions, which have no
                session.
            emitLog (bool): Whether to emit the structured `admin_audit` log
                line. Callers that already emit their own line pass False so a
                single event does not appear twice in the log stream.
        """
        fields = list(changedFields or [])
        if admin is not None:
            actorType = "admin"
            adminId = admin.adminId
            sessionId = admin.sessionId
            email = admin.email
        else:
            actorType = "cli"
            adminId = None
            sessionId = None
            email = actorEmail or ADMIN_AUDIT_SYSTEM_ACTOR

        if emitLog:
            event = logger.bind(
                adminId=adminId,
                sessionId=sessionId,
                actorType=actorType,
                actorEmail=email,
                action=action,
                targetType=targetType,
                targetId=targetId,
                changedFields=fields,
                outcome=outcome,
            )
            if outcome == "compensation_failed":
                event.critical("admin_audit")
            else:
                event.info("admin_audit")

        try:
            self.client.table(ADMIN_AUDIT_TABLE).insert({
                "admin_id": adminId,
                "admin_email": email,
                "session_id": sessionId,
                "actor_type": actorType,
                "action": action,
                "target_type": targetType,
                "target_id": targetId,
                "changed_fields": fields,
                "outcome": outcome,
                "created_at": self.nowProvider().isoformat(),
            }).execute()
        except Exception as exc:
            logger.error(
                "Durable admin audit write failed for {} on {}: {}",
                action,
                targetType,
                type(exc).__name__,
            )

    def listEvents(
        self,
        limit: int = ADMIN_AUDIT_DEFAULT_PAGE_SIZE,
        offset: int = 0,
        targetType: str | None = None,
        outcome: str | None = None,
    ) -> list[dict]:
        """
        Return audit events newest first.

        Args:
            limit (int): Page size, clamped to 1..200.
            offset (int): Rows to skip, clamped to >= 0.
            targetType (str | None): Optional exact-match filter.
            outcome (str | None): Optional exact-match filter.

        Returns:
            list[dict]: Serialized audit events.
        """
        boundedLimit = max(1, min(int(limit), ADMIN_AUDIT_MAX_PAGE_SIZE))
        boundedOffset = max(0, int(offset))

        query = (
            self.client.table(ADMIN_AUDIT_TABLE)
            .select(ADMIN_AUDIT_SELECT)
            .order("created_at", desc=True)
        )
        if targetType is not None:
            query = query.eq("target_type", targetType)
        if outcome is not None:
            query = query.eq("outcome", outcome)

        try:
            rows = query.range(
                boundedOffset, boundedOffset + boundedLimit - 1
            ).execute().data or []
        except Exception as exc:
            logger.error("Admin audit list failed: {}", type(exc).__name__)
            raise AdminApiError(500, "Failed to list audit events") from exc

        return [_serializeAuditEvent(row) for row in rows]


def _serializeAuditEvent(row: dict) -> dict:
    result = {field: row.get(field) for field in ADMIN_AUDIT_FIELDS}
    result["changed_fields"] = json.dumps(
        result["changed_fields"] or [], separators=(",", ":")
    )
    return result


_adminAuditService: AdminAuditService | None = None


def getAdminAuditService() -> AdminAuditService:
    global _adminAuditService
    if _adminAuditService is None:
        _adminAuditService = AdminAuditService()
    return _adminAuditService
