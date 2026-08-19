import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.services.adminAuditService import (
    ADMIN_AUDIT_TABLE,
    AdminAuditService,
    getAdminAuditService,
)
from api.services.adminAuthService import AdminContext


FIXED_NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

ADMIN_CONTEXT = AdminContext(
    adminId="4fa8af6f-71f4-4b05-b26f-fc89ac72a371",
    email="admin@example.com",
    name="Admin",
    sessionId="130516b0-f229-4f37-98b6-6e0be8ff9dd4",
    token="admin-token",
)


class FixedClock:
    def __init__(self, current=FIXED_NOW):
        self.current = current

    def __call__(self):
        return self.current


class FakeAuditQuery:
    def __init__(self, client):
        self.client = client
        self.operation = "select"
        self.payload = None
        self.filters = []
        self.rangeBounds = None
        self.orderBy = None

    def insert(self, payload):
        self.operation = "insert"
        self.payload = dict(payload)
        return self

    def select(self, _fields):
        self.operation = "select"
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, column, desc=False):
        self.orderBy = (column, desc)
        return self

    def range(self, start, end):
        self.rangeBounds = (start, end)
        return self

    def execute(self):
        if self.client.failInsert and self.operation == "insert":
            raise RuntimeError("audit backend down; secret=must-not-be-logged")
        if self.client.failSelect and self.operation == "select":
            raise RuntimeError("audit backend down; secret=must-not-be-logged")
        if self.operation == "insert":
            self.client.rows[ADMIN_AUDIT_TABLE].append(self.payload)
            return SimpleNamespace(data=[dict(self.payload)])
        self.client.lastRange = self.rangeBounds
        self.client.lastOrder = self.orderBy
        self.client.lastFilters = list(self.filters)
        matching = [
            row for row in self.client.rows[ADMIN_AUDIT_TABLE]
            if all(row.get(field) == value for field, value in self.filters)
        ]
        return SimpleNamespace(data=[dict(row) for row in matching])


class FakeAuditClient:
    def __init__(self, failInsert=False, failSelect=False, rows=None):
        self.rows = {ADMIN_AUDIT_TABLE: list(rows or [])}
        self.failInsert = failInsert
        self.failSelect = failSelect
        self.lastRange = None
        self.lastOrder = None
        self.lastFilters = None

    def table(self, name):
        if name != ADMIN_AUDIT_TABLE:
            raise AssertionError(f"Unexpected table: {name}")
        return FakeAuditQuery(self)


def buildService(**kwargs):
    client = FakeAuditClient(**kwargs)
    return AdminAuditService(client=client, nowProvider=FixedClock()), client


def test_record_writes_durable_row():
    service, client = buildService()

    service.record(
        action="user.update",
        targetType="user",
        targetId="user-1",
        changedFields=["email"],
        outcome="success",
        admin=ADMIN_CONTEXT,
    )

    assert len(client.rows[ADMIN_AUDIT_TABLE]) == 1
    row = client.rows[ADMIN_AUDIT_TABLE][0]
    assert row["actor_type"] == "admin"
    assert row["admin_id"] == ADMIN_CONTEXT.adminId
    assert row["admin_email"] == ADMIN_CONTEXT.email
    assert row["session_id"] == ADMIN_CONTEXT.sessionId
    assert row["action"] == "user.update"
    assert row["target_type"] == "user"
    assert row["target_id"] == "user-1"
    assert row["changed_fields"] == ["email"]
    assert row["outcome"] == "success"
    assert row["created_at"] == FIXED_NOW.isoformat()


def test_record_never_raises_when_durable_write_fails():
    service, client = buildService(failInsert=True)

    service.record(
        action="user.update",
        targetType="user",
        targetId="user-1",
        changedFields=["email"],
        outcome="success",
        admin=ADMIN_CONTEXT,
    )

    assert client.rows[ADMIN_AUDIT_TABLE] == []


def test_record_does_not_leak_backend_detail_on_failure():
    from loguru import logger

    service, _ = buildService(failInsert=True)
    captured = []
    sinkId = logger.add(captured.append, level="ERROR")
    try:
        service.record(
            action="user.update",
            targetType="user",
            targetId="user-1",
            changedFields=["email"],
            outcome="success",
            admin=ADMIN_CONTEXT,
        )
    finally:
        logger.remove(sinkId)

    combined = "".join(captured)
    assert combined, "expected the failed durable write to be logged"
    assert "must-not-be-logged" not in combined
    assert "RuntimeError" in combined


def test_cli_actor_has_no_admin_or_session_id():
    service, client = buildService()

    service.record(
        action="admin.deactivate",
        targetType="admin",
        targetId="admin-9",
        changedFields=["is_active"],
        outcome="success",
        actorEmail="ops@example.com",
    )

    row = client.rows[ADMIN_AUDIT_TABLE][0]
    assert row["actor_type"] == "cli"
    assert row["admin_id"] is None
    assert row["session_id"] is None
    assert row["admin_email"] == "ops@example.com"


def test_changed_fields_defaults_to_empty_list():
    service, client = buildService()

    service.record(
        action="admin.list",
        targetType="admin",
        targetId=None,
        changedFields=None,
        outcome="success",
        actorEmail="ops@example.com",
    )

    assert client.rows[ADMIN_AUDIT_TABLE][0]["changed_fields"] == []


def test_record_without_actor_falls_back_to_system_identity():
    service, client = buildService()

    service.record(
        action="admin.create",
        targetType="admin",
        targetId="admin-1",
        changedFields=[],
        outcome="success",
    )

    row = client.rows[ADMIN_AUDIT_TABLE][0]
    assert row["actor_type"] == "cli"
    assert row["admin_email"] == "system"


def test_list_events_clamps_limit_and_orders_newest_first():
    service, client = buildService()

    service.listEvents(limit=5000, offset=0)

    assert client.lastRange == (0, 199)
    assert client.lastOrder == ("created_at", True)


def test_list_events_clamps_lower_bounds():
    service, client = buildService()

    service.listEvents(limit=0, offset=-10)

    assert client.lastRange == (0, 0)


def test_list_events_applies_filters():
    service, client = buildService()

    service.listEvents(limit=10, offset=0, targetType="user", outcome="failed")

    assert ("target_type", "user") in client.lastFilters
    assert ("outcome", "failed") in client.lastFilters


def test_list_events_serializes_changed_fields_as_json_string():
    rows = [{
        "id": "audit-1",
        "admin_id": ADMIN_CONTEXT.adminId,
        "admin_email": ADMIN_CONTEXT.email,
        "session_id": ADMIN_CONTEXT.sessionId,
        "actor_type": "admin",
        "action": "user.update",
        "target_type": "user",
        "target_id": "user-1",
        "changed_fields": ["email", "fullName"],
        "outcome": "success",
        "created_at": FIXED_NOW.isoformat(),
    }]
    service = AdminAuditService(client=FakeAuditClient(rows=rows))

    events = service.listEvents(limit=10, offset=0)

    assert events[0]["changed_fields"] == '["email","fullName"]'


def test_list_events_raises_admin_error_when_backend_fails():
    from api.adminErrors import AdminApiError

    service, _ = buildService(failSelect=True)

    with pytest.raises(AdminApiError) as excinfo:
        service.listEvents(limit=10, offset=0)

    assert excinfo.value.statusCode == 500
    assert excinfo.value.message == "Failed to list audit events"


def test_get_admin_audit_service_is_singleton():
    assert getAdminAuditService() is getAdminAuditService()
