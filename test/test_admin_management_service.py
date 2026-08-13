import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.adminErrors import AdminApiError
from api.adminModels import AdminUserPatch
from api.services.adminAuthService import AdminContext
from api.services.adminManagementService import (
    ADMIN_USER_SELECT,
    AdminManagementService,
)


ADMIN_CONTEXT = AdminContext(
    adminId="admin-1",
    email="admin@example.com",
    name="Admin",
    sessionId="admin-session-1",
    token="secret-admin-token",
)

EXPECTED_USER_FIELDS = {
    "userId", "email", "fullName", "phoneNumber", "profileImage",
    "onboarded", "currentWorkspaceId", "companyName", "role", "profileBio",
    "usage", "industryType", "companySize", "country", "goals", "source",
}


def userRow(userId="user-1", email="old@example.com", **overrides):
    row = {
        "userId": userId,
        "email": email,
        "fullName": "Old Name",
        "phoneNumber": "+10000000000",
        "profileImage": "avatar.png",
        "onboarded": 1,
        "currentWorkspaceId": "workspace-1",
        "companyName": "Nubrix",
        "role": "Analyst",
        "profileBio": "Profile",
        "usage": "Business",
        "industryType": "Technology",
        "companySize": "1-10",
        "country": "India",
        "goals": "Insights",
        "source": "Referral",
        "password": "must-never-leave-storage",
        "internalFlag": "private",
    }
    row.update(overrides)
    return row


class FakeQuery:
    def __init__(self, client, tableName):
        self.client = client
        self.tableName = tableName
        self.operation = "select"
        self.payload = None
        self.filters = []
        self.orderColumn = None
        self.rangeBounds = None
        self.selectFields = None

    def select(self, fields):
        self.operation = "select"
        self.selectFields = fields
        self.client.selects.setdefault(self.tableName, []).append(fields)
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def ilike(self, field, value):
        self.filters.append(("ilike", field, value))
        return self

    def neq(self, field, value):
        self.filters.append(("neq", field, value))
        return self

    def order(self, column):
        self.orderColumn = column
        return self

    def range(self, start, end):
        self.rangeBounds = (start, end)
        self.client.ranges.setdefault(self.tableName, []).append((start, end))
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = dict(payload)
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def execute(self):
        if self.operation == "update" and self.tableName == "Users":
            if self.client.failNextUsersUpdate:
                self.client.failNextUsersUpdate = False
                raise RuntimeError(
                    "Users update failed: old@example.com -> new@example.com"
                )
        if self.operation == "delete" and self.tableName == "Sessions":
            if self.client.failSessionDelete:
                raise RuntimeError("session deletion failed: secret-session-value")

        matching = [
            row for row in self.client.rows[self.tableName]
            if self._matches(row)
        ]
        if self.orderColumn is not None:
            matching.sort(key=lambda row: str(row.get(self.orderColumn, "")))
        if self.rangeBounds is not None:
            start, end = self.rangeBounds
            matching = matching[start:end + 1]

        if self.operation == "update":
            for row in matching:
                row.update(self.payload)
            self.client.updates.append(
                (self.tableName, dict(self.payload), self._simpleFilters())
            )
        elif self.operation == "delete":
            self.client.deletes.append((self.tableName, self._simpleFilters()))
            self.client.rows[self.tableName] = [
                row for row in self.client.rows[self.tableName]
                if not self._matches(row)
            ]

        return SimpleNamespace(data=[self._project(row) for row in matching])

    def _matches(self, row):
        for operation, field, value in self.filters:
            actual = row.get(field)
            if operation == "eq" and actual != value:
                return False
            if operation == "neq" and actual == value:
                return False
            if operation == "ilike":
                pattern = re.escape(str(value)).replace(r"\%", ".*").replace(r"\_", ".")
                if re.fullmatch(pattern, str(actual), re.IGNORECASE) is None:
                    return False
        return True

    def _simpleFilters(self):
        return [(field, value) for operation, field, value in self.filters if operation == "eq"]

    def _project(self, row):
        if self.operation != "select" or self.selectFields is None:
            return dict(row)
        fields = self.selectFields.split(",")
        return {field: row.get(field) for field in fields}


class FakeClient:
    def __init__(self, users):
        self.rows = {"Users": list(users), "Sessions": []}
        self.selects = {}
        self.ranges = {}
        self.updates = []
        self.deletes = []
        self.failNextUsersUpdate = False
        self.failSessionDelete = False
        self.auth = SimpleNamespace(
            admin=SimpleNamespace(update_user_by_id=MagicMock())
        )

    def table(self, tableName):
        if tableName not in self.rows:
            raise AssertionError(f"Unexpected table: {tableName}")
        return FakeQuery(self, tableName)

    def lastUpdate(self, tableName):
        return next(
            payload for table, payload, _filters in reversed(self.updates)
            if table == tableName
        )

    def deletedFilters(self, tableName):
        return next(
            filters for table, filters in reversed(self.deletes)
            if table == tableName
        )


class FakeBoundLogger:
    def __init__(self, logger, context):
        self.logger = logger
        self.context = context

    def info(self, message):
        self.logger.records.append(("info", message, self.context))

    def critical(self, message):
        self.logger.records.append(("critical", message, self.context))


class FakeLogger:
    def __init__(self):
        self.records = []

    def bind(self, **context):
        return FakeBoundLogger(self, context)


def test_list_users_batches_and_never_selects_sensitive_fields():
    rows = [
        userRow(f"user-{index}", f"User{1001 - index:04d}@Example.com")
        for index in range(1002)
    ]
    client = FakeClient(rows)
    service = AdminManagementService(client=client)

    users = service.listUsers()

    assert len(users) == 1002
    assert all("password" not in user for user in users)
    assert client.selects["Users"] == [ADMIN_USER_SELECT, ADMIN_USER_SELECT]
    assert client.ranges["Users"] == [(0, 999), (1000, 1999)]
    assert [user["email"] for user in users] == sorted(
        (user["email"] for user in users), key=str.casefold
    )


def test_list_users_serializes_exactly_sixteen_fields_and_normalizes_boolean():
    client = FakeClient([userRow(onboarded=0, profileBio=None)])

    users = AdminManagementService(client=client).listUsers()

    assert set(users[0]) == EXPECTED_USER_FIELDS
    assert len(users[0]) == 16
    assert users[0]["onboarded"] is False
    assert users[0]["profileBio"] is None


def test_update_user_applies_only_fields_explicitly_supplied():
    client = FakeClient([userRow()])
    service = AdminManagementService(client=client)
    patchPayload = AdminUserPatch.model_validate({"fullName": None, "onboarded": True})

    updated = service.updateUser("user-1", patchPayload, ADMIN_CONTEXT)

    assert client.lastUpdate("Users") == {"fullName": None, "onboarded": True}
    assert updated["fullName"] is None
    assert set(updated) == EXPECTED_USER_FIELDS


def test_update_user_returns_404_when_target_is_missing():
    client = FakeClient([])

    with pytest.raises(AdminApiError) as captured:
        AdminManagementService(client=client).updateUser(
            "missing-user",
            AdminUserPatch.model_validate({"fullName": "New Name"}),
            ADMIN_CONTEXT,
        )

    assert captured.value.statusCode == 404
    assert captured.value.message == "User not found"
    assert client.updates == []


def test_update_user_rejects_case_insensitive_duplicate_email():
    client = FakeClient([
        userRow(),
        userRow("user-2", "NEW@example.COM"),
    ])

    with pytest.raises(AdminApiError) as captured:
        AdminManagementService(client=client).updateUser(
            "user-1",
            AdminUserPatch.model_validate({"email": "New@Example.com"}),
            ADMIN_CONTEXT,
        )

    assert captured.value.statusCode == 409
    assert captured.value.message == "A user with this email already exists"
    client.auth.admin.update_user_by_id.assert_not_called()
    assert client.updates == []


def test_email_update_user_syncs_auth_confirms_and_revokes_product_sessions():
    client = FakeClient([userRow()])
    client.rows["Sessions"] = [
        {"id": "session-1", "userId": "user-1"},
        {"id": "session-2", "userId": "user-2"},
    ]

    updated = AdminManagementService(client=client).updateUser(
        "user-1",
        AdminUserPatch.model_validate({"email": "New@Example.com"}),
        ADMIN_CONTEXT,
    )

    client.auth.admin.update_user_by_id.assert_called_once_with(
        "user-1", {"email": "new@example.com", "email_confirm": True}
    )
    assert client.deletedFilters("Sessions") == [("userId", "user-1")]
    assert updated["email"] == "new@example.com"


def test_users_failure_rolls_back_auth_email():
    client = FakeClient([userRow()])
    client.failNextUsersUpdate = True

    with pytest.raises(AdminApiError) as captured:
        AdminManagementService(client=client).updateUser(
            "user-1",
            AdminUserPatch.model_validate({"email": "new@example.com"}),
            ADMIN_CONTEXT,
        )

    assert captured.value.statusCode == 500
    assert captured.value.message == "Failed to update user"
    assert client.auth.admin.update_user_by_id.call_args_list[-1].args == (
        "user-1", {"email": "old@example.com", "email_confirm": True}
    )


def test_update_user_logs_redacted_critical_audit_when_auth_rollback_fails():
    client = FakeClient([userRow()])
    client.failNextUsersUpdate = True
    client.auth.admin.update_user_by_id.side_effect = [
        SimpleNamespace(user={"id": "user-1"}),
        RuntimeError("rollback leaked old@example.com and secret-auth-value"),
    ]
    auditLogger = FakeLogger()

    with patch("api.services.adminManagementService.logger", auditLogger):
        with pytest.raises(AdminApiError) as captured:
            AdminManagementService(client=client).updateUser(
                "user-1",
                AdminUserPatch.model_validate({"email": "new@example.com"}),
                ADMIN_CONTEXT,
            )

    assert captured.value.statusCode == 500
    critical = [record for record in auditLogger.records if record[0] == "critical"]
    assert critical == [(
        "critical",
        "admin_audit",
        {
            "adminId": "admin-1",
            "sessionId": "admin-session-1",
            "targetType": "user",
            "targetId": "user-1",
            "changedFields": ["email"],
            "outcome": "compensation_failed",
        },
    )]
    renderedLogs = repr(auditLogger.records)
    assert "old@example.com" not in renderedLogs
    assert "new@example.com" not in renderedLogs
    assert "secret-admin-token" not in renderedLogs
    assert "secret-auth-value" not in renderedLogs


def test_update_user_returns_generic_500_when_session_deletion_fails():
    client = FakeClient([userRow()])
    client.rows["Sessions"] = [{"id": "session-1", "userId": "user-1"}]
    client.failSessionDelete = True
    auditLogger = FakeLogger()

    with patch("api.services.adminManagementService.logger", auditLogger):
        with pytest.raises(AdminApiError) as captured:
            AdminManagementService(client=client).updateUser(
                "user-1",
                AdminUserPatch.model_validate({"email": "new@example.com"}),
                ADMIN_CONTEXT,
            )

    assert captured.value.statusCode == 500
    assert captured.value.message == "Failed to update user"
    assert any(
        record[2]["outcome"] == "side_effect_failed"
        for record in auditLogger.records
    )
    assert "secret-session-value" not in repr(auditLogger.records)


def test_update_user_writes_redacted_success_audit_with_sorted_field_names():
    client = FakeClient([userRow()])
    auditLogger = FakeLogger()

    with patch("api.services.adminManagementService.logger", auditLogger):
        AdminManagementService(client=client).updateUser(
            "user-1",
            AdminUserPatch.model_validate({
                "role": "Owner",
                "fullName": "New Name",
            }),
            ADMIN_CONTEXT,
        )

    assert auditLogger.records == [(
        "info",
        "admin_audit",
        {
            "adminId": "admin-1",
            "sessionId": "admin-session-1",
            "targetType": "user",
            "targetId": "user-1",
            "changedFields": ["fullName", "role"],
            "outcome": "success",
        },
    )]
    renderedLogs = repr(auditLogger.records)
    assert "New Name" not in renderedLogs
    assert "Owner" not in renderedLogs
    assert "secret-admin-token" not in renderedLogs
