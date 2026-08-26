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


def extendedItem(userId="free-user", syncStatus="PENDING"):
    return {
        "id": f"item-{userId}",
        "user_id": userId,
        "outcome": "EXTENDED",
        "days_added": 5,
        "previous_expiry": datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc),
        "new_expiry": datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
        "credit_sync_status": syncStatus,
        "credit_quota": 1_000,
        "credit_topup_tokens": 250,
        "credit_period_end": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        "credit_generation": 4,
        "access_still_banned": False,
        "error_code": None,
    }


def failedItem(userId="paid-user", code="PAID_SUBSCRIPTION_NOT_ELIGIBLE"):
    return {
        "id": f"item-{userId}",
        "user_id": userId,
        "outcome": "FAILED",
        "days_added": None,
        "previous_expiry": None,
        "new_expiry": None,
        "credit_sync_status": "NOT_APPLICABLE",
        "credit_quota": None,
        "credit_topup_tokens": None,
        "credit_period_end": None,
        "credit_generation": None,
        "access_still_banned": False,
        "error_code": code,
    }


class FakeRepository:
    def __init__(self):
        self.batch = None
        self.items = {}
        self.extendResults = {}
        self.extendCalls = []
        self.marked = []
        self.completed = []

    def createOrGetBatch(self, **kwargs):
        if self.batch is None:
            self.batch = {
                "id": KEY,
                "idempotency_key": kwargs["idempotencyKey"],
                "request_hash": kwargs["requestHash"],
                "days": kwargs["days"],
            }
        return dict(self.batch)

    def getItem(self, batchId, userId):
        return self.items.get(userId)

    def extendUser(self, **kwargs):
        self.extendCalls.append(kwargs)
        result = self.extendResults[kwargs["userId"]]
        if isinstance(result, Exception):
            raise result
        self.items[kwargs["userId"]] = dict(result)
        return dict(result)

    def synchronizeCreditItem(self, itemId, syncCallback):
        for item in self.items.values():
            if item["id"] != itemId:
                continue
            result = syncCallback(dict(item))
            if result == "APPLIED":
                item["credit_sync_status"] = "SYNCED"
                self.marked.append(itemId)
            elif result == "STALE":
                item["credit_sync_status"] = "SUPERSEDED"
            return dict(item)
        return None

    def recordFailure(self, batchId, userId, errorCode):
        item = failedItem(userId, errorCode)
        self.items[userId] = item
        return dict(item)

    def completeBatch(self, batchId):
        self.completed.append(batchId)


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
        enqueue=enqueue or (lambda _itemId: None),
        nowProvider=lambda: NOW,
    )


def test_mixed_batch_extends_free_user_refreshes_credits_and_reports_paid_user():
    repository = FakeRepository()
    repository.extendResults = {
        "free-user": extendedItem(),
        "paid-user": failedItem(),
    }
    credits = FakeCreditService()
    service = buildService(repository, credits)
    payload = AdminFreeTrialExtensionRequest(
        userIds=["free-user", "paid-user"], days=5
    )

    result = service.extend(payload, KEY, ADMIN)

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["summary"] == {
        "requested": 2,
        "extended": 1,
        "failed": 1,
        "creditSyncPending": 0,
    }
    assert result["results"][0]["creditSyncStatus"] == "SYNCED"
    assert result["results"][1]["errorCode"] == (
        "PAID_SUBSCRIPTION_NOT_ELIGIBLE"
    )
    assert credits.calls == [{
        "userId": "free-user",
        "quota": 1_000,
        "topupTokens": 250,
        "periodEnd": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        "generation": 4,
    }]
    assert repository.marked == ["item-free-user"]
    assert repository.completed == [KEY]


def test_replay_uses_existing_items_and_never_adds_days_twice():
    repository = FakeRepository()
    payload = AdminFreeTrialExtensionRequest(userIds=["free-user"], days=5)
    service = buildService(repository)
    repository.batch = {
        "id": KEY,
        "idempotency_key": KEY,
        "request_hash": service.requestHash(payload),
        "days": 5,
    }
    repository.items["free-user"] = extendedItem(syncStatus="SYNCED")

    result = service.extend(payload, KEY, ADMIN)

    assert result["status"] == "COMPLETED"
    assert repository.extendCalls == []


def test_reused_idempotency_key_with_different_payload_is_conflict():
    repository = FakeRepository()
    original = AdminFreeTrialExtensionRequest(userIds=["free-user"], days=4)
    service = buildService(repository)
    repository.batch = {
        "id": KEY,
        "idempotency_key": KEY,
        "request_hash": service.requestHash(original),
        "days": 4,
    }

    with pytest.raises(AdminApiError) as error:
        service.extend(
            AdminFreeTrialExtensionRequest(userIds=["free-user"], days=5),
            KEY,
            ADMIN,
        )

    assert error.value.statusCode == 409


def test_redis_failure_keeps_extension_successful_and_queues_repair():
    repository = FakeRepository()
    repository.extendResults = {"free-user": extendedItem()}
    queued = []
    service = buildService(
        repository,
        creditService=FakeCreditService(succeeds=False),
        enqueue=queued.append,
    )

    result = service.extend(
        AdminFreeTrialExtensionRequest(userIds=["free-user"], days=5),
        KEY,
        ADMIN,
    )

    assert result["status"] == "COMPLETED"
    assert result["summary"]["creditSyncPending"] == 1
    assert result["results"][0]["outcome"] == "EXTENDED"
    assert result["results"][0]["creditSyncStatus"] == "PENDING"
    assert queued == ["item-free-user"]


def test_one_database_failure_is_recorded_without_aborting_later_users():
    repository = FakeRepository()
    repository.extendResults = {
        "broken-user": RuntimeError("database unavailable"),
        "free-user": extendedItem(),
    }
    service = buildService(repository)

    result = service.extend(
        AdminFreeTrialExtensionRequest(
            userIds=["broken-user", "free-user"], days=5
        ),
        KEY,
        ADMIN,
    )

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["results"][0]["errorCode"] == "EXTENSION_FAILED"
    assert result["results"][1]["outcome"] == "EXTENDED"


def test_invalid_idempotency_key_is_rejected_before_database_work():
    repository = FakeRepository()

    with pytest.raises(AdminApiError) as error:
        buildService(repository).extend(
            AdminFreeTrialExtensionRequest(userIds=["free-user"], days=5),
            "not-a-uuid",
            ADMIN,
        )

    assert error.value.statusCode == 422
    assert repository.batch is None
