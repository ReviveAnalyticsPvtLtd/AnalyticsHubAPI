import hashlib
import hmac
import pytest

from api.adminErrors import AdminApiError
from api.adminModels import (
    AdminUserErasureBatchConfirmRequest,
    AdminUserErasureBatchPreviewRequest,
)
from api.services.adminAuthService import AdminContext


KEY = "8cfdb150-417d-47ab-acd1-fef39d2bc14e"
SECOND_KEY = "97857c51-0a63-4569-b087-8756c67d0746"
STATUS_BATCH_ID = "414c6f2b-a630-49c2-89eb-444514479384"
SECRET = "independent-batch-test-secret"
ADMIN = AdminContext(
    adminId="admin-1",
    email="admin@example.com",
    name="Admin",
    sessionId="session-1",
    token="admin-token",
)


def _fingerprint(userId: str) -> str:
    return hmac.new(
        SECRET.encode("utf-8"), userId.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class FakeRepository:
    def __init__(self, classifications=None):
        self.byKey = {}
        self.batches = {}
        self.createCalls = []
        self.classifications = classifications or {}

    def findByIdempotency(self, idempotencyKey):
        return self.byKey.get(str(idempotencyKey))

    def createPreview(
        self,
        userItems,
        adminId,
        idempotencyKey,
        requestHash,
        reason,
    ):
        self.createCalls.append({
            "userItems": [dict(item) for item in userItems],
            "adminId": adminId,
            "idempotencyKey": idempotencyKey,
            "requestHash": requestHash,
            "reason": reason,
        })
        items = []
        readyCount = 0
        for item in userItems:
            classification, requestId = self.classifications.get(
                item["userId"], ("READY", None)
            )
            readyCount += classification == "READY"
            items.append({
                "id": f"item-{item['ordinal'] + 1}",
                "ordinal": item["ordinal"],
                "target_user_id": item["userId"],
                "subject_fingerprint": item["subjectFingerprint"],
                "classification": classification,
                "request_id": requestId,
                "error_code": (
                    "USER_NOT_FOUND"
                    if classification == "USER_NOT_FOUND"
                    else None
                ),
                "request_status": (
                    "IN_PROGRESS"
                    if classification == "ALREADY_IN_PROGRESS"
                    else "COMPLETED"
                    if classification == "ALREADY_COMPLETED"
                    else None
                ),
            })
        batch = {
            "id": "batch-1",
            "requested_by": adminId,
            "idempotency_key": idempotencyKey,
            "request_hash": requestHash,
            "status": "PREVIEWED",
            "reason": reason,
            "requested_count": len(items),
            "ready_count": readyCount,
            "expires_at": "2026-08-26T10:15:00+00:00",
            "created_at": "2026-08-26T10:00:00+00:00",
            "items": items,
        }
        self.byKey[idempotencyKey] = batch
        self.batches[batch["id"]] = batch
        return batch

    def getBatch(self, batchId):
        return self.batches.get(str(batchId))


class FakeAudit:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


class ConcurrentCreateRepository(FakeRepository):
    def __init__(self, concurrentBatch):
        super().__init__()
        self.concurrentBatch = concurrentBatch
        self.lookupCalls = []

    def findByIdempotency(self, idempotencyKey):
        self.lookupCalls.append(idempotencyKey)
        if len(self.lookupCalls) == 1:
            return None
        return self.concurrentBatch

    def createPreview(self, **kwargs):
        self.createCalls.append(kwargs)
        raise RuntimeError("duplicate key value violates unique constraint")


def _service(monkeypatch, repository=None):
    from api.services.userErasureBatchService import UserErasureBatchService

    monkeypatch.setenv("USER_ERASURE_ENABLED", "true")
    monkeypatch.setenv("USER_ERASURE_HMAC_SECRET", SECRET)
    repository = repository or FakeRepository()
    audit = FakeAudit()
    return UserErasureBatchService(repository=repository, auditService=audit), repository, audit


def _payload(userIds, reason=None):
    return AdminUserErasureBatchPreviewRequest.model_validate({
        "userIds": userIds,
        "reason": reason,
    })


def test_preview_hashes_canonical_subject_set_and_replays_same_key(monkeypatch):
    service, repository, _audit = _service(monkeypatch)

    first = service.preview(
        _payload(["user-2", "user-1"], "Support request"), KEY, ADMIN
    )
    second = service.preview(
        _payload(["user-1", "user-2"], "Support request"), KEY, ADMIN
    )

    assert second == first
    assert repository.createCalls == [{
        "userItems": [
            {
                "ordinal": 0,
                "userId": "user-2",
                "subjectFingerprint": _fingerprint("user-2"),
            },
            {
                "ordinal": 1,
                "userId": "user-1",
                "subjectFingerprint": _fingerprint("user-1"),
            },
        ],
        "adminId": "admin-1",
        "idempotencyKey": KEY,
        "requestHash": "8e4b030c9b6e8104fce6e3ba88b9046815af9f523574530d194516161a9fde7e",
        "reason": "Support request",
    }]
    assert first == {
        "batchId": "batch-1",
        "status": "PREVIEWED",
        "expiresAt": "2026-08-26T10:15:00+00:00",
        "requiredConfirmation": "ERASE 2 USERS",
        "summary": {
            "requested": 2,
            "ready": 2,
            "alreadyInProgress": 0,
            "alreadyCompleted": 0,
            "notFound": 0,
        },
        "results": [
            {
                "itemId": "item-1",
                "userId": "user-2",
                "status": "READY",
                "requestId": None,
                "errorCode": None,
            },
            {
                "itemId": "item-2",
                "userId": "user-1",
                "status": "READY",
                "requestId": None,
                "errorCode": None,
            },
        ],
    }


@pytest.mark.parametrize(
    "key",
    ["", "not-a-uuid", "8cfdb150417d47abacd1fef39d2bc14e-extra", None],
)
def test_preview_rejects_invalid_idempotency_key(monkeypatch, key):
    service, repository, _audit = _service(monkeypatch)

    with pytest.raises(AdminApiError) as captured:
        service.preview(_payload(["user-1"]), key, ADMIN)

    assert captured.value.statusCode == 422
    assert captured.value.message == "Invalid Idempotency-Key header"
    assert repository.createCalls == []


@pytest.mark.parametrize(
    "secondPayload",
    [
        _payload(["user-1", "user-3"], "Support request"),
        _payload(["user-1", "user-2"], "Another reason"),
    ],
)
def test_preview_rejects_same_key_for_different_request(monkeypatch, secondPayload):
    service, repository, _audit = _service(monkeypatch)
    service.preview(
        _payload(["user-1", "user-2"], "Support request"), KEY, ADMIN
    )

    with pytest.raises(AdminApiError) as captured:
        service.preview(secondPayload, KEY, ADMIN)

    assert captured.value.statusCode == 409
    assert captured.value.message == "Idempotency key is already in use"
    assert len(repository.createCalls) == 1


def test_preview_reloads_matching_row_after_concurrent_create_conflict(monkeypatch):
    concurrentBatch = {
        "id": "batch-concurrent",
        "request_hash": "a20e4763a7f99bfd5b75ab1a595978fa55c0a0c83b5a6bd156cc7b50a3427ae4",
        "status": "PREVIEWED",
        "expires_at": "2026-08-26T10:15:00+00:00",
        "items": [{
            "id": "item-concurrent",
            "target_user_id": "user-1",
            "classification": "READY",
            "request_id": None,
            "request_status": None,
            "error_code": None,
        }],
    }
    repository = ConcurrentCreateRepository(concurrentBatch)
    service, _repository, audit = _service(monkeypatch, repository)

    result = service.preview(_payload(["user-1"]), KEY.upper(), ADMIN)

    assert result["batchId"] == "batch-concurrent"
    assert repository.lookupCalls == [KEY, KEY]
    assert len(repository.createCalls) == 1
    assert audit.calls == []


def test_preview_rejects_different_row_after_concurrent_create_conflict(monkeypatch):
    repository = ConcurrentCreateRepository({
        "id": "batch-concurrent",
        "request_hash": "f" * 64,
        "status": "PREVIEWED",
        "expires_at": "2026-08-26T10:15:00+00:00",
        "items": [],
    })
    service, _repository, _audit = _service(monkeypatch, repository)

    with pytest.raises(AdminApiError) as captured:
        service.preview(_payload(["user-1"]), KEY, ADMIN)

    assert captured.value.statusCode == 409
    assert captured.value.message == "Idempotency key is already in use"
    assert repository.lookupCalls == [KEY, KEY]


def test_preview_preserves_safe_500_when_failed_create_has_no_concurrent_row(monkeypatch):
    repository = ConcurrentCreateRepository(None)
    service, _repository, _audit = _service(monkeypatch, repository)

    with pytest.raises(AdminApiError) as captured:
        service.preview(_payload(["user-1"]), KEY, ADMIN)

    assert captured.value.statusCode == 500
    assert captured.value.message == "Failed to preview user erasure batch"
    assert "unique constraint" not in str(captured.value)
    assert repository.lookupCalls == [KEY, KEY]


def test_preview_feature_gate_is_fail_closed(monkeypatch):
    service, repository, _audit = _service(monkeypatch)
    monkeypatch.setenv("USER_ERASURE_ENABLED", "false")

    with pytest.raises(AdminApiError) as captured:
        service.preview(_payload(["user-1"]), KEY, ADMIN)

    assert captured.value.statusCode == 503
    assert captured.value.message == "User erasure is not enabled"
    assert repository.createCalls == []


def test_preview_requires_hmac_secret_before_repository_access(monkeypatch):
    service, repository, _audit = _service(monkeypatch)
    monkeypatch.delenv("USER_ERASURE_HMAC_SECRET")

    with pytest.raises(AdminApiError) as captured:
        service.preview(_payload(["user-1"]), KEY, ADMIN)

    assert captured.value.statusCode == 500
    assert captured.value.message == "User erasure is unavailable"
    assert repository.createCalls == []


def test_preview_without_ready_subjects_has_no_confirmation(monkeypatch):
    repository = FakeRepository(classifications={
        "active": ("ALREADY_IN_PROGRESS", "request-active"),
        "done": ("ALREADY_COMPLETED", "request-done"),
        "missing": ("USER_NOT_FOUND", None),
    })
    service, _repository, _audit = _service(monkeypatch, repository)

    result = service.preview(
        _payload(["active", "done", "missing"]), KEY, ADMIN
    )

    assert result["requiredConfirmation"] is None
    assert result["summary"] == {
        "requested": 3,
        "ready": 0,
        "alreadyInProgress": 1,
        "alreadyCompleted": 1,
        "notFound": 1,
    }
    assert [item["status"] for item in result["results"]] == [
        "IN_PROGRESS",
        "COMPLETED",
        "USER_NOT_FOUND",
    ]


def test_preview_uses_exact_singular_confirmation(monkeypatch):
    service, _repository, _audit = _service(monkeypatch)

    result = service.preview(_payload(["user-1"]), KEY, ADMIN)

    assert result["requiredConfirmation"] == "ERASE 1 USER"


def test_preview_audit_contains_batch_id_and_counts_only(monkeypatch):
    repository = FakeRepository(classifications={
        "missing": ("USER_NOT_FOUND", None),
    })
    service, _repository, audit = _service(monkeypatch, repository)

    service.preview(
        _payload(["user-1", "missing"], "must not appear in audit"), KEY, ADMIN
    )

    assert audit.calls == [{
        "action": "user.erasure_batch.preview",
        "targetType": "user_erasure_batch",
        "targetId": "batch-1",
        "changedFields": ["status"],
        "outcome": "success",
        "admin": ADMIN,
        "details": {
            "requested": 2,
            "ready": 1,
            "alreadyInProgress": 0,
            "alreadyCompleted": 0,
            "notFound": 1,
        },
    }]
    assert "user-1" not in repr(audit.calls)
    assert "missing" not in repr(audit.calls)
    assert "must not appear" not in repr(audit.calls)


def test_get_status_is_readable_when_gate_disabled_and_sanitizes_items(monkeypatch):
    service, repository, _audit = _service(monkeypatch)
    monkeypatch.setenv("USER_ERASURE_ENABLED", "false")
    repository.batches[STATUS_BATCH_ID] = {
        "id": STATUS_BATCH_ID,
        "requested_by": "admin-private",
        "request_hash": "a" * 64,
        "reason": "private reason",
        "status": "IN_PROGRESS",
        "requested_count": 3,
        "ready_count": 1,
        "expires_at": "2026-08-26T10:15:00+00:00",
        "items": [
            {
                "id": "item-1",
                "target_user_id": "user-1",
                "subject_fingerprint": "private-fingerprint",
                "classification": "READY",
                "request_id": "request-1",
                "request_status": "PARTIALLY_FAILED",
                "error_code": None,
            },
            {
                "id": "item-2",
                "target_user_id": None,
                "subject_fingerprint": "private-fingerprint-2",
                "classification": "ALREADY_COMPLETED",
                "request_id": "request-2",
                "request_status": "COMPLETED",
                "error_code": None,
            },
            {
                "id": "item-3",
                "target_user_id": "missing-user",
                "subject_fingerprint": "private-fingerprint-3",
                "classification": "USER_NOT_FOUND",
                "request_id": None,
                "request_status": None,
                "error_code": "USER_NOT_FOUND",
            },
        ],
    }

    result = service.getStatus(STATUS_BATCH_ID.upper(), ADMIN)

    assert result == {
        "batchId": STATUS_BATCH_ID,
        "status": "PARTIALLY_FAILED",
        "expiresAt": "2026-08-26T10:15:00+00:00",
        "requiredConfirmation": None,
        "summary": {
            "requested": 3,
            "ready": 1,
            "alreadyInProgress": 0,
            "alreadyCompleted": 1,
            "notFound": 1,
        },
        "results": [
            {
                "itemId": "item-1",
                "userId": "user-1",
                "status": "PARTIALLY_FAILED",
                "requestId": "request-1",
                "errorCode": None,
            },
            {
                "itemId": "item-2",
                "userId": None,
                "status": "COMPLETED",
                "requestId": "request-2",
                "errorCode": None,
            },
            {
                "itemId": "item-3",
                "userId": "missing-user",
                "status": "USER_NOT_FOUND",
                "requestId": None,
                "errorCode": "USER_NOT_FOUND",
            },
        ],
    }
    assert "private" not in repr(result)


def test_get_status_returns_safe_not_found_and_persistence_errors(monkeypatch):
    class FailingRepository(FakeRepository):
        def getBatch(self, batchId):
            if batchId == SECOND_KEY:
                raise RuntimeError("postgresql://secret-password")
            return None

    service, _repository, _audit = _service(monkeypatch, FailingRepository())

    with pytest.raises(AdminApiError) as missing:
        service.getStatus(KEY, ADMIN)
    assert missing.value.statusCode == 404
    assert missing.value.message == "User erasure batch not found"

    with pytest.raises(AdminApiError) as failed:
        service.getStatus(SECOND_KEY, ADMIN)
    assert failed.value.statusCode == 500
    assert failed.value.message == "Failed to read user erasure batch"
    assert "password" not in str(failed.value)


@pytest.mark.parametrize("operation", ["status", "confirm"])
def test_batch_operations_reject_malformed_batch_id_before_repository_access(
    monkeypatch, operation
):
    class NoAccessRepository(FakeRepository):
        def getBatch(self, batchId):
            raise AssertionError("repository must not be reached")

        def confirmBatch(self, **kwargs):
            raise AssertionError("repository must not be reached")

    service, _repository, audit = _service(monkeypatch, NoAccessRepository())

    with pytest.raises(AdminApiError) as captured:
        if operation == "status":
            service.getStatus("not-a-uuid", ADMIN)
        else:
            service.confirm("not-a-uuid", _confirm_payload(), ADMIN)

    assert (captured.value.statusCode, captured.value.message) == (
        422,
        "Invalid user erasure batch ID",
    )
    assert audit.calls == []


def test_confirm_rejects_malformed_batch_id_even_when_initiation_gate_is_disabled(
    monkeypatch,
):
    repository = ConfirmServiceRepository()
    service, _repository, audit = _service(monkeypatch, repository)
    monkeypatch.setenv("USER_ERASURE_ENABLED", "false")

    with pytest.raises(AdminApiError) as captured:
        service.confirm("not-a-uuid", _confirm_payload(), ADMIN)

    assert (captured.value.statusCode, captured.value.message) == (
        422,
        "Invalid user erasure batch ID",
    )
    assert repository.confirmCalls == []
    assert audit.calls == []


def test_service_factory_returns_single_instance(monkeypatch):
    import api.services.userErasureBatchService as module

    monkeypatch.setattr(module, "_userErasureBatchService", None)
    first = module.getUserErasureBatchService()
    second = module.getUserErasureBatchService()

    assert isinstance(first, module.UserErasureBatchService)
    assert second is first


class ConfirmServiceRepository:
    def __init__(self, batch=None, error=None):
        self.batch = batch or {
            "id": "414c6f2b-a630-49c2-89eb-444514479384",
            "status": "IN_PROGRESS",
            "expires_at": "2026-08-26T10:15:00+00:00",
            "items": [
                {
                    "id": "item-1",
                    "ordinal": 0,
                    "target_user_id": "user-private-1",
                    "classification": "READY",
                    "request_id": "request-pending",
                    "request_status": "PENDING",
                    "error_code": None,
                },
                {
                    "id": "item-2",
                    "ordinal": 1,
                    "target_user_id": "user-private-2",
                    "classification": "ALREADY_COMPLETED",
                    "request_id": "request-completed",
                    "request_status": "COMPLETED",
                    "error_code": None,
                },
            ],
        }
        self.error = error
        self.confirmCalls = []
        self.committed = False

    def confirmBatch(self, batchId, adminId, sessionId, confirmation):
        self.confirmCalls.append({
            "batchId": batchId,
            "adminId": adminId,
            "sessionId": sessionId,
            "confirmation": confirmation,
        })
        if self.error is not None:
            raise self.error
        self.committed = True
        return self.batch


def _confirm_payload(value="ERASE 1 USER"):
    return AdminUserErasureBatchConfirmRequest(confirmation=value)


def test_confirm_enqueues_unfinished_children_only_after_commit_and_keeps_queue_failure_durable(monkeypatch):
    repository = ConfirmServiceRepository()
    queueCalls = []

    def failingQueue(requestId):
        assert repository.committed is True
        queueCalls.append(requestId)
        raise RuntimeError("redis password must not leak")

    service, _ignored, audit = _service(monkeypatch, repository)
    service.enqueue = failingQueue

    result = service.confirm(
        repository.batch["id"], _confirm_payload(), ADMIN
    )

    assert repository.confirmCalls == [{
        "batchId": repository.batch["id"],
        "adminId": ADMIN.adminId,
        "sessionId": ADMIN.sessionId,
        "confirmation": "ERASE 1 USER",
    }]
    assert queueCalls == ["request-pending"]
    assert result["status"] == "IN_PROGRESS"
    assert [item["requestId"] for item in result["results"]] == [
        "request-pending",
        "request-completed",
    ]
    assert audit.calls == [{
        "action": "user.erasure_batch.confirm",
        "targetType": "user_erasure_batch",
        "targetId": repository.batch["id"],
        "changedFields": ["status", "confirmed_at"],
        "outcome": "side_effect_failed",
        "admin": ADMIN,
        "details": {"requested": 2, "linked": 2, "queued": 0},
    }]
    assert "user-private" not in repr(audit.calls)
    assert "password" not in repr(audit.calls)


def test_confirm_replay_returns_same_ids_and_safely_requeues_unfinished_children(monkeypatch):
    repository = ConfirmServiceRepository()
    queued = []
    service, _ignored, audit = _service(monkeypatch, repository)
    service.enqueue = queued.append

    first = service.confirm(repository.batch["id"], _confirm_payload(), ADMIN)
    second = service.confirm(repository.batch["id"], _confirm_payload(), ADMIN)

    assert first == second
    assert [item["requestId"] for item in second["results"]] == [
        "request-pending",
        "request-completed",
    ]
    assert queued == ["request-pending", "request-pending"]
    assert len(repository.confirmCalls) == 2
    assert [call["outcome"] for call in audit.calls] == ["success", "success"]
    assert [call["details"]["queued"] for call in audit.calls] == [1, 1]


def test_confirm_normalizes_batch_uuid_before_repository_access(monkeypatch):
    repository = ConfirmServiceRepository()
    service, _ignored, _audit = _service(monkeypatch, repository)
    service.enqueue = lambda _requestId: None

    service.confirm(repository.batch["id"].upper(), _confirm_payload(), ADMIN)

    assert repository.confirmCalls[0]["batchId"] == repository.batch["id"]


def test_confirm_feature_gate_is_fail_closed_before_repository_access(monkeypatch):
    repository = ConfirmServiceRepository()
    service, _ignored, audit = _service(monkeypatch, repository)
    monkeypatch.setenv("USER_ERASURE_ENABLED", "false")

    with pytest.raises(AdminApiError) as captured:
        service.confirm(repository.batch["id"], _confirm_payload(), ADMIN)

    assert (captured.value.statusCode, captured.value.message) == (
        503,
        "User erasure is not enabled",
    )
    assert repository.confirmCalls == []
    assert audit.calls == []


def test_confirm_preserves_admin_errors_and_sanitizes_unexpected_database_errors(monkeypatch):
    forbidden = ConfirmServiceRepository(
        error=AdminApiError(403, "Only the preview creator can confirm it")
    )
    service, _ignored, _audit = _service(monkeypatch, forbidden)

    with pytest.raises(AdminApiError) as captured:
        service.confirm(forbidden.batch["id"], _confirm_payload(), ADMIN)
    assert captured.value.statusCode == 403
    assert captured.value.message == "Only the preview creator can confirm it"

    failed = ConfirmServiceRepository(
        error=RuntimeError("postgresql://secret-password")
    )
    service, _ignored, audit = _service(monkeypatch, failed)

    with pytest.raises(AdminApiError) as captured:
        service.confirm(failed.batch["id"], _confirm_payload(), ADMIN)
    assert captured.value.statusCode == 500
    assert captured.value.message == "Failed to confirm user erasure batch"
    assert "password" not in str(captured.value)
    assert audit.calls == []


class ReconcileServiceRepository:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.batch = {
            "id": STATUS_BATCH_ID,
            "status": "COMPLETED",
            "reason": None,
            "expires_at": "2026-08-26T10:15:00+00:00",
            "items": [{
                "id": "item-1",
                "target_user_id": None,
                "classification": "READY",
                "request_id": "request-1",
                "request_status": "COMPLETED",
                "error_code": None,
            }],
        }

    def listReconcilable(self, limit):
        self.calls.append(("list", limit))
        return [STATUS_BATCH_ID]

    def reconcileBatch(self, batchId):
        self.calls.append(("reconcile", batchId))
        if self.error is not None:
            raise self.error
        return self.batch


def test_internal_reconciliation_serializes_scrubbed_terminal_batch(monkeypatch):
    repository = ReconcileServiceRepository()
    service, _ignored, _audit = _service(monkeypatch, repository)

    assert service.listReconcilable(25) == [STATUS_BATCH_ID]
    result = service.reconcile(STATUS_BATCH_ID.upper())

    assert repository.calls == [
        ("list", 25),
        ("reconcile", STATUS_BATCH_ID),
    ]
    assert result["status"] == "COMPLETED"
    assert result["results"][0]["userId"] is None
    assert "reason" not in result


def test_internal_reconciliation_validates_id_and_sanitizes_database_errors(monkeypatch):
    repository = ReconcileServiceRepository()
    service, _ignored, _audit = _service(monkeypatch, repository)

    with pytest.raises(AdminApiError) as captured:
        service.reconcile("not-a-uuid")
    assert captured.value.statusCode == 422
    assert repository.calls == []

    repository.error = RuntimeError("database password must not leak")
    with pytest.raises(AdminApiError) as captured:
        service.reconcile(STATUS_BATCH_ID)
    assert captured.value.statusCode == 500
    assert captured.value.message == "Failed to reconcile user erasure batch"
    assert "password" not in str(captured.value)
