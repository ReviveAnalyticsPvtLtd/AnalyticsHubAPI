from types import SimpleNamespace

import pytest

from api.adminErrors import AdminApiError
from api.adminModels import AdminUserErasureRequest
from api.services.adminAuthService import AdminContext


ADMIN = AdminContext(
    adminId="admin-1",
    email="admin@example.com",
    name="Admin",
    sessionId="session-1",
    token="admin-token",
)


class FakeRepository:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.byKey = {}
        self.requests = {}

    def findByIdempotency(self, idempotencyKey):
        return self.byKey.get(str(idempotencyKey))

    def createRequest(
        self,
        userId,
        subjectFingerprint,
        adminId,
        idempotencyKey,
        reason,
    ):
        self.events.append(("create", userId))
        row = {
            "id": "request-1",
            "target_user_id": userId,
            "subject_fingerprint": subjectFingerprint,
            "idempotency_key": str(idempotencyKey),
            "status": "PENDING",
            "created_at": "2026-08-24T10:00:00+00:00",
        }
        self.byKey[str(idempotencyKey)] = row
        self.requests[row["id"]] = {**row, "steps": []}
        return row

    def getRequest(self, requestId):
        return self.requests.get(requestId)


class FakeAudit:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


def _service(monkeypatch, repository=None, queue=None):
    from api.services.userErasureService import UserErasureService

    monkeypatch.setenv("USER_ERASURE_ENABLED", "true")
    monkeypatch.setenv("USER_ERASURE_HMAC_SECRET", "independent-test-secret")
    events = []
    repository = repository or FakeRepository(events)
    audit = FakeAudit()
    queue = queue or (lambda requestId: events.append(("queue", requestId)))
    return (
        UserErasureService(
            repository=repository,
            auditService=audit,
            enqueue=queue,
        ),
        repository,
        audit,
        events,
    )


def test_start_erasure_queues_after_durable_request_without_duplicate_access_call(monkeypatch):
    service, _repository, audit, events = _service(monkeypatch)

    result = service.start(
        "user-1",
        AdminUserErasureRequest.model_validate({
            "confirmation": "ERASE",
            "reason": "  Customer request  ",
        }),
        "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        ADMIN,
    )

    assert [event[0] for event in events] == ["create", "queue"]
    assert result == {
        "requestId": "request-1",
        "userId": "user-1",
        "status": "PENDING",
        "createdAt": "2026-08-24T10:00:00+00:00",
    }
    assert audit.calls[-1]["action"] == "user.erasure.start"
    assert audit.calls[-1]["targetId"] == "request-1"
    assert audit.calls[-1]["details"] == {
        "targetFingerprint": audit.calls[-1]["details"]["targetFingerprint"],
        "queued": True,
    }
    assert len(audit.calls[-1]["details"]["targetFingerprint"]) == 64


def test_start_erasure_replays_same_idempotency_key_without_recreating_request(monkeypatch):
    service, repository, _audit, events = _service(monkeypatch)
    payload = AdminUserErasureRequest.model_validate({"confirmation": "ERASE"})
    key = "8cfdb150-417d-47ab-acd1-fef39d2bc14e"

    first = service.start("user-1", payload, key, ADMIN)
    second = service.start("user-1", payload, key, ADMIN)

    assert second == first
    assert [event[0] for event in events].count("create") == 1
    assert [event[0] for event in events].count("queue") == 2
    assert repository.findByIdempotency(key)["target_user_id"] == "user-1"


def test_start_erasure_rejects_idempotency_key_reused_for_another_user(monkeypatch):
    service, _repository, _audit, _events = _service(monkeypatch)
    payload = AdminUserErasureRequest.model_validate({"confirmation": "ERASE"})
    key = "8cfdb150-417d-47ab-acd1-fef39d2bc14e"
    service.start("user-1", payload, key, ADMIN)

    with pytest.raises(AdminApiError) as captured:
        service.start("user-2", payload, key, ADMIN)

    assert captured.value.statusCode == 409
    assert captured.value.message == "Idempotency key is already in use"


def test_start_erasure_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("USER_ERASURE_ENABLED", raising=False)
    monkeypatch.setenv("USER_ERASURE_HMAC_SECRET", "test-secret")
    events = []
    repository = FakeRepository(events)

    from api.services.userErasureService import UserErasureService

    service = UserErasureService(
        repository=repository,
        auditService=FakeAudit(),
        enqueue=lambda requestId: events.append(("queue", requestId)),
    )

    with pytest.raises(AdminApiError) as captured:
        service.start(
            "user-1",
            AdminUserErasureRequest.model_validate({"confirmation": "ERASE"}),
            "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
            ADMIN,
        )

    assert captured.value.statusCode == 503
    assert captured.value.message == "User erasure is not enabled"
    assert events == []


def test_queue_failure_keeps_request_accepted_and_records_safe_warning(monkeypatch):
    def unavailable(_requestId):
        raise RuntimeError("redis://password-must-not-leak")

    service, repository, audit, _events = _service(
        monkeypatch,
        queue=unavailable,
    )

    result = service.start(
        "user-1",
        AdminUserErasureRequest.model_validate({"confirmation": "ERASE"}),
        "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        ADMIN,
    )

    assert result["requestId"] in repository.requests
    assert audit.calls[-1]["outcome"] == "side_effect_failed"
    assert audit.calls[-1]["details"]["queued"] is False
    assert "password" not in repr(audit.calls[-1])
