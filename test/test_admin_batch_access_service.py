import pytest

from api.adminErrors import AdminApiError
from api.adminModels import AdminUserAccessBatchRequest
from api.services.adminAuthService import AdminContext
from api.services.adminManagementService import AdminManagementService


ADMIN = AdminContext(
    adminId="admin-1",
    email="admin@example.com",
    name="Admin",
    sessionId="session-1",
    token="admin-token",
)


def _updatedAccess(userId: str, banned: bool, warnings: list[str] | None = None):
    return {
        "userId": userId,
        "isBanned": banned,
        "bannedAt": "2026-08-26T10:00:00+00:00" if banned else None,
        "bannedBy": ADMIN.adminId if banned else None,
        "banReason": "support action" if banned else None,
        "sessionsRevoked": 1,
        "supabaseAuthSynced": not warnings,
        "warnings": warnings or [],
    }


def test_set_users_access_processes_all_users_and_reports_failures(monkeypatch):
    """A missing user must not stop later persisted access updates."""
    service = AdminManagementService()

    def set_one(userId, patch, admin):
        if userId == "missing":
            raise AdminApiError(404, "User not found")
        return _updatedAccess(
            userId,
            patch.banned,
            ["Supabase Auth synchronization failed"]
            if userId == "auth-warning" else None,
        )

    monkeypatch.setattr(service, "setUserAccess", set_one)

    result = service.setUsersAccess(
        AdminUserAccessBatchRequest(
            userIds=["user-1", "missing", "auth-warning"], banned=True
        ),
        ADMIN,
    )

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["summary"] == {
        "requested": 3, "updated": 2, "failed": 1, "withWarnings": 1
    }
    assert [item["outcome"] for item in result["results"]] == [
        "UPDATED", "FAILED", "UPDATED"
    ]
    assert result["results"][1] == {
        "userId": "missing",
        "outcome": "FAILED",
        "isBanned": None,
        "bannedAt": None,
        "bannedBy": None,
        "banReason": None,
        "sessionsRevoked": 0,
        "supabaseAuthSynced": False,
        "warnings": [],
        "errorCode": "USER_NOT_FOUND",
    }


@pytest.mark.parametrize(
    ("exception", "errorCode"),
    [
        (AdminApiError(409, "User erasure is in progress"), "ERASURE_IN_PROGRESS"),
        (RuntimeError("database password=must-not-leak"), "ACCESS_UPDATE_FAILED"),
    ],
)
def test_set_users_access_returns_stable_safe_failure_codes(
    monkeypatch, exception, errorCode
):
    """Known and unexpected failures must have safe, stable per-user codes."""
    service = AdminManagementService()

    def fail(*_args):
        raise exception

    monkeypatch.setattr(service, "setUserAccess", fail)

    result = service.setUsersAccess(
        AdminUserAccessBatchRequest(userIds=["user-1"], banned=True), ADMIN
    )

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["summary"] == {
        "requested": 1, "updated": 0, "failed": 1, "withWarnings": 0
    }
    assert result["results"][0]["errorCode"] == errorCode
    assert "password" not in str(result)


def test_set_users_access_returns_completed_for_all_success(monkeypatch):
    """A fully persisted batch without warnings is completed."""
    service = AdminManagementService()
    monkeypatch.setattr(
        service, "setUserAccess", lambda userId, patch, admin: _updatedAccess(
            userId, patch.banned
        )
    )

    result = service.setUsersAccess(
        AdminUserAccessBatchRequest(userIds=["user-1", "user-2"], banned=True),
        ADMIN,
    )

    assert result["status"] == "COMPLETED"
    assert result["summary"] == {
        "requested": 2, "updated": 2, "failed": 0, "withWarnings": 0
    }


def test_set_users_access_restores_in_input_order(monkeypatch):
    """Restore batches use the requested state for every normalized ID in order."""
    service = AdminManagementService()
    calls = []

    def set_one(userId, patch, admin):
        calls.append((userId, patch.banned, patch.reason, admin))
        return _updatedAccess(userId, patch.banned)

    monkeypatch.setattr(service, "setUserAccess", set_one)

    result = service.setUsersAccess(
        AdminUserAccessBatchRequest(
            userIds=[" user-2 ", "user-1", "user-2"],
            banned=False,
            reason="  ignored on restore  ",
        ),
        ADMIN,
    )

    assert [item["userId"] for item in result["results"]] == ["user-2", "user-1"]
    assert calls == [
        ("user-2", False, "ignored on restore", ADMIN),
        ("user-1", False, "ignored on restore", ADMIN),
    ]
    assert [item["isBanned"] for item in result["results"]] == [False, False]
