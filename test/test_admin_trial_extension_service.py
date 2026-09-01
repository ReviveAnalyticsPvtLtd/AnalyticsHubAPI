from datetime import datetime, timezone

import pytest

from api.adminErrors import AdminApiError
from api.adminModels import AdminFreeTrialExtensionRequest
from api.services.adminAuthService import AdminContext
from api.services.adminTrialExtensionService import AdminTrialExtensionService


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
KEY = "f53b33cd-219e-4c70-b5c2-43d956591fa5"
ADMIN = AdminContext(
    adminId="admin-1",
    email="admin@example.com",
    name="Admin",
    sessionId="session-1",
    token="token",
)


def pendingExtension(userId="free-user"):
    return {
        "id": KEY,
        "idempotency_key": KEY,
        "request_hash": None,
        "user_id": userId,
        "outcome": "PENDING",
        "days_added": None,
        "previous_expiry": None,
        "new_expiry": None,
        "credit_sync_status": "NOT_APPLICABLE",
        "credit_quota": None,
        "credit_topup_tokens": None,
        "credit_period_end": None,
        "credit_generation": None,
        "access_still_banned": False,
        "error_code": None,
    }


def extendedExtension(userId="free-user", syncStatus="PENDING"):
    return {
        **pendingExtension(userId),
        "outcome": "EXTENDED",
        "days_added": 5,
        "previous_expiry": datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc),
        "new_expiry": datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
        "credit_sync_status": syncStatus,
        "credit_quota": 1_000,
        "credit_topup_tokens": 250,
        "credit_period_end": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        "credit_generation": 4,
    }


def failedExtension(userId="paid-user", code="PAID_SUBSCRIPTION_NOT_ELIGIBLE"):
    return {
        **pendingExtension(userId),
        "outcome": "FAILED",
        "error_code": code,
    }


class FakeRepository:
    def __init__(self):
        self.extension = None
        self.extendResult = None
        self.extendCalls = []
        self.marked = []
        self.recordedFailures = []

    def createOrGetExtension(self, **kwargs):
        if self.extension is None:
            self.extension = pendingExtension(kwargs["userId"])
            self.extension["request_hash"] = kwargs["requestHash"]
        return dict(self.extension)

    def extendUser(self, **kwargs):
        self.extendCalls.append(kwargs)
        if isinstance(self.extendResult, Exception):
            raise self.extendResult
        self.extension = dict(self.extendResult)
        return dict(self.extension)

    def synchronizeCreditExtension(self, extensionId, syncCallback):
        if self.extension is None or self.extension["id"] != extensionId:
            return None
        result = syncCallback(dict(self.extension))
        if result == "APPLIED":
            self.extension["credit_sync_status"] = "SYNCED"
            self.marked.append(extensionId)
        elif result == "STALE":
            self.extension["credit_sync_status"] = "SUPERSEDED"
        return dict(self.extension)

    def recordFailure(self, extensionId, errorCode):
        self.recordedFailures.append((extensionId, errorCode))
        self.extension = failedExtension(self.extension["user_id"], errorCode)
        return dict(self.extension)


class FakeCreditService:
    def __init__(self, succeeds=True):
        self.result = "APPLIED" if succeeds else "FAILED"
        self.calls = []

    def refreshTrialCreditsCache(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeAuditService:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


def buildService(repository, creditService=None, enqueue=None):
    return AdminTrialExtensionService(
        repository=repository,
        creditService=creditService or FakeCreditService(),
        auditService=FakeAuditService(),
        enqueue=enqueue or (lambda _extensionId: None),
        nowProvider=lambda: NOW,
    )


def test_single_user_extension_refreshes_credits_and_returns_one_result():
    repository = FakeRepository()
    repository.extendResult = extendedExtension()
    credits = FakeCreditService()
    service = buildService(repository, credits)

    result = service.extend(
        AdminFreeTrialExtensionRequest(userId="free-user", days=5), KEY, ADMIN
    )

    assert result == {
        "extensionId": KEY,
        "userId": "free-user",
        "outcome": "EXTENDED",
        "daysAdded": 5,
        "previousExpiry": "2026-08-25T10:00:00+00:00",
        "newExpiry": "2026-08-30T10:00:00+00:00",
        "creditsRefreshed": True,
        "creditSyncStatus": "SYNCED",
        "accessStillBanned": False,
        "errorCode": None,
    }
    assert credits.calls == [{
        "userId": "free-user",
        "quota": 1_000,
        "topupTokens": 250,
        "periodEnd": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        "generation": 4,
    }]
    assert repository.marked == [KEY]


def test_replay_returns_completed_extension_without_adding_days_twice():
    repository = FakeRepository()
    payload = AdminFreeTrialExtensionRequest(userId="free-user", days=5)
    service = buildService(repository)
    repository.extension = extendedExtension(syncStatus="SYNCED")
    repository.extension["request_hash"] = service.requestHash(payload)

    result = service.extend(payload, KEY, ADMIN)

    assert result["extensionId"] == KEY
    assert result["outcome"] == "EXTENDED"
    assert repository.extendCalls == []


def test_reused_idempotency_key_with_different_payload_is_conflict():
    repository = FakeRepository()
    original = AdminFreeTrialExtensionRequest(userId="free-user", days=4)
    service = buildService(repository)
    repository.extension = pendingExtension()
    repository.extension["request_hash"] = service.requestHash(original)

    with pytest.raises(AdminApiError) as error:
        service.extend(
            AdminFreeTrialExtensionRequest(userId="free-user", days=5),
            KEY,
            ADMIN,
        )

    assert error.value.statusCode == 409


def test_redis_failure_keeps_extension_successful_and_queues_repair():
    repository = FakeRepository()
    repository.extendResult = extendedExtension()
    queued = []
    service = buildService(
        repository,
        creditService=FakeCreditService(succeeds=False),
        enqueue=queued.append,
    )

    result = service.extend(
        AdminFreeTrialExtensionRequest(userId="free-user", days=5), KEY, ADMIN
    )

    assert result["outcome"] == "EXTENDED"
    assert result["creditSyncStatus"] == "PENDING"
    assert queued == [KEY]


def test_database_failure_returns_single_failed_result():
    repository = FakeRepository()
    repository.extendResult = RuntimeError("database unavailable")
    service = buildService(repository)

    result = service.extend(
        AdminFreeTrialExtensionRequest(userId="broken-user", days=5), KEY, ADMIN
    )

    assert result["outcome"] == "FAILED"
    assert result["errorCode"] == "EXTENSION_FAILED"
    assert repository.recordedFailures == [(KEY, "EXTENSION_FAILED")]


def test_invalid_idempotency_key_is_rejected_before_database_work():
    repository = FakeRepository()

    with pytest.raises(AdminApiError) as error:
        buildService(repository).extend(
            AdminFreeTrialExtensionRequest(userId="free-user", days=5),
            "not-a-uuid",
            ADMIN,
        )

    assert error.value.statusCode == 422
    assert repository.extension is None


def test_extension_persistence_failure_uses_single_user_error_message():
    repository = FakeRepository()

    def failCreate(**_kwargs):
        raise RuntimeError("database unavailable")

    repository.createOrGetExtension = failCreate

    with pytest.raises(AdminApiError) as error:
        buildService(repository).extend(
            AdminFreeTrialExtensionRequest(userId="free-user", days=5),
            KEY,
            ADMIN,
        )

    assert error.value.statusCode == 500
    assert error.value.message == "Failed to extend free trial"
