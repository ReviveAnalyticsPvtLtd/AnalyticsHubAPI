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


class FakeAccessService:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def setUserAccess(self, userId, payload, admin):
        self.events.append(("ban", userId))
        self.calls.append((userId, payload, admin))
        return {"isBanned": True}


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
    access = FakeAccessService(events)
    audit = FakeAudit()
    queue = queue or (lambda requestId: events.append(("queue", requestId)))
    return (
        UserErasureService(
            repository=repository,
            accessService=access,
            auditService=audit,
            enqueue=queue,
        ),
        repository,
        access,
        audit,
        events,
    )


def test_start_erasure_persists_atomic_ban_before_external_auth_sync_and_queue(monkeypatch):
    service, _repository, access, audit, events = _service(monkeypatch)

    result = service.start(
        "user-1",
        AdminUserErasureRequest.model_validate({
            "confirmation": "ERASE",
            "reason": "  Customer request  ",
        }),
        "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        ADMIN,
    )

    assert [event[0] for event in events] == ["create", "ban", "queue"]
    assert access.calls[0][1].banned is True
    assert access.calls[0][1].reason == "Customer request"
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


def test_start_erasure_replays_same_idempotency_key_without_rebanning(monkeypatch):
    service, repository, access, _audit, events = _service(monkeypatch)
    payload = AdminUserErasureRequest.model_validate({"confirmation": "ERASE"})
    key = "8cfdb150-417d-47ab-acd1-fef39d2bc14e"

    first = service.start("user-1", payload, key, ADMIN)
    second = service.start("user-1", payload, key, ADMIN)

    assert second == first
    assert len(access.calls) == 1
    assert [event[0] for event in events].count("create") == 1
    assert [event[0] for event in events].count("queue") == 2
    assert repository.findByIdempotency(key)["target_user_id"] == "user-1"


def test_start_erasure_rejects_idempotency_key_reused_for_another_user(monkeypatch):
    service, _repository, access, _audit, _events = _service(monkeypatch)
    payload = AdminUserErasureRequest.model_validate({"confirmation": "ERASE"})
    key = "8cfdb150-417d-47ab-acd1-fef39d2bc14e"
    service.start("user-1", payload, key, ADMIN)

    with pytest.raises(AdminApiError) as captured:
        service.start("user-2", payload, key, ADMIN)

    assert captured.value.statusCode == 409
    assert captured.value.message == "Idempotency key is already in use"
    assert len(access.calls) == 1


def test_start_erasure_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("USER_ERASURE_ENABLED", raising=False)
    monkeypatch.setenv("USER_ERASURE_HMAC_SECRET", "test-secret")
    events = []
    repository = FakeRepository(events)

    from api.services.userErasureService import UserErasureService

    service = UserErasureService(
        repository=repository,
        accessService=FakeAccessService(events),
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

    service, repository, _access, audit, _events = _service(
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


def test_get_status_returns_sanitized_steps_and_404(monkeypatch):
    repository = FakeRepository()
    repository.requests["request-1"] = {
        "id": "request-1",
        "target_user_id": "user-1",
        "reason": "must-not-leak",
        "resource_manifest": {"paths": ["must-not-leak.csv"]},
        "status": "IN_PROGRESS",
        "created_at": "2026-08-24T10:00:00+00:00",
        "started_at": "2026-08-24T10:00:02+00:00",
        "completed_at": None,
        "last_error_code": None,
        "steps": [{
            "step_name": "inventory",
            "status": "COMPLETED",
            "attempt_count": 1,
            "last_error_code": None,
        }],
    }
    service, *_ = _service(monkeypatch, repository=repository)

    result = service.getStatus("request-1")

    assert result == {
        "requestId": "request-1",
        "status": "IN_PROGRESS",
        "createdAt": "2026-08-24T10:00:00+00:00",
        "startedAt": "2026-08-24T10:00:02+00:00",
        "completedAt": None,
        "lastErrorCode": None,
        "steps": [{
            "name": "inventory",
            "status": "COMPLETED",
            "attempts": 1,
            "lastErrorCode": None,
        }],
    }
    assert "must-not-leak" not in repr(result)

    with pytest.raises(AdminApiError) as captured:
        service.getStatus("missing")
    assert captured.value.statusCode == 404
    assert captured.value.message == "Erasure request not found"
