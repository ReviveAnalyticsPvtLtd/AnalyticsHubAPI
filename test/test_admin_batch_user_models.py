import pytest
from pydantic import ValidationError
from types import SimpleNamespace

from api.adminModels import (
    AdminUserAccessBatchRequest,
    AdminUserAccessBatchResponse,
    AdminUserErasureBatchConfirmRequest,
    AdminUserErasureBatchItemView,
    AdminUserErasureBatchPreviewRequest,
    AdminUserErasureBatchView,
)
from api.services.userErasureBatchService import UserErasureBatchService


def test_access_batch_normalizes_ids_and_optional_reason():
    payload = AdminUserAccessBatchRequest.model_validate({
        "userIds": [" user-1 ", "user-1", "user-2"],
        "banned": True,
        "reason": "  support action  ",
    })
    assert payload.userIds == ["user-1", "user-2"]
    assert payload.reason == "support action"


@pytest.mark.parametrize("user_ids", [[], [""], ["x" * 129], [str(i) for i in range(101)]])
def test_access_batch_rejects_invalid_user_ids(user_ids):
    with pytest.raises(ValidationError):
        AdminUserAccessBatchRequest.model_validate({"userIds": user_ids, "banned": True})


def test_erasure_preview_caps_users_and_builds_strict_confirmation_model():
    preview = AdminUserErasureBatchPreviewRequest.model_validate({
        "userIds": [" user-1 ", "user-1", "user-2"],
        "reason": "   ",
    })
    assert preview.userIds == ["user-1", "user-2"]
    assert preview.reason is None
    assert AdminUserErasureBatchConfirmRequest.model_validate({
        "confirmation": "ERASE 1 USER"
    }).confirmation == "ERASE 1 USER"
    assert AdminUserErasureBatchConfirmRequest.model_validate({
        "confirmation": "ERASE 2 USERS"
    }).confirmation == "ERASE 2 USERS"

    for confirmation in ("ERASE 1 USERS", "ERASE USERS", "ERASE 26 USERS"):
        with pytest.raises(ValidationError):
            AdminUserErasureBatchConfirmRequest.model_validate({"confirmation": confirmation})


def test_erasure_preview_rejects_more_than_25_users():
    with pytest.raises(ValidationError):
        AdminUserErasureBatchPreviewRequest.model_validate({
            "userIds": [str(i) for i in range(26)]
        })


def test_completed_batch_item_allows_scrubbed_user_id_and_nullable_request():
    item = AdminUserErasureBatchItemView.model_validate({
        "itemId": "item-1",
        "userId": None,
        "status": "COMPLETED",
        "requestId": None,
        "errorCode": None,
    })
    assert item.userId is None
    assert item.requestId is None
    assert "reason" not in item.model_dump()


def test_batch_views_reject_private_fields():
    item = {
        "itemId": "item-1", "userId": "user-1", "status": "USER_NOT_FOUND",
        "requestId": None, "errorCode": "USER_NOT_FOUND",
    }
    summary = {
        "requested": 1,
        "ready": 0,
        "alreadyInProgress": 0,
        "alreadyCompleted": 0,
        "notFound": 1,
    }
    response_payload = {
        "status": "PARTIAL_SUCCESS",
        "summary": {"requested": 1, "updated": 0, "failed": 1, "withWarnings": 0},
        "results": [{
            "userId": "user-1", "outcome": "FAILED", "isBanned": None,
            "bannedAt": None, "bannedBy": None, "banReason": None,
            "sessionsRevoked": 0, "supabaseAuthSynced": False, "warnings": [],
            "errorCode": "FAILED",
        }],
    }
    response = AdminUserAccessBatchResponse.model_validate(response_payload)
    for forbidden in ("fingerprint", "requestHash", "reason", "adminSessionAt", "rawError"):
        with pytest.raises(ValidationError):
            AdminUserAccessBatchResponse.model_validate({
                **response_payload,
                forbidden: "must-not-be-exposed",
            })

    with pytest.raises(ValidationError):
        AdminUserAccessBatchResponse.model_validate({**response_payload, "batchId": "must-not-exist"})

    view = AdminUserErasureBatchView.model_validate({
        "batchId": "batch-1", "status": "PREVIEWED",
        "expiresAt": "2026-08-26T10:15:00+00:00",
        "requiredConfirmation": None,
        "summary": summary, "results": [item],
    })
    for forbidden in ("fingerprint", "requestHash", "reason", "adminSessionAt", "rawError"):
        with pytest.raises(ValidationError):
            AdminUserErasureBatchView.model_validate({
                **view.model_dump(), forbidden: "must-not-be-exposed",
            })


class _BatchModelRepository:
    def __init__(self, batch):
        self.batch = batch

    def findByIdempotency(self, _key):
        return None

    def createPreview(self, **_kwargs):
        return self.batch

    def getBatch(self, _batchId):
        return self.batch


class _BatchModelAudit:
    def record(self, **_kwargs):
        return None


def test_batch_view_validates_actual_preview_service_payload(monkeypatch):
    monkeypatch.setenv("USER_ERASURE_ENABLED", "true")
    monkeypatch.setenv("USER_ERASURE_HMAC_SECRET", "model-contract-secret")
    repository = _BatchModelRepository({
        "id": "batch-preview",
        "status": "PREVIEWED",
        "expires_at": "2026-08-26T10:15:00+00:00",
        "items": [{
            "id": "item-1",
            "target_user_id": "user-1",
            "classification": "READY",
            "request_id": None,
            "request_status": None,
            "error_code": None,
        }],
    })
    service = UserErasureBatchService(
        repository=repository,
        auditService=_BatchModelAudit(),
    )

    payload = service.preview(
        AdminUserErasureBatchPreviewRequest(userIds=["user-1"]),
        "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        SimpleNamespace(adminId="admin-1"),
    )
    view = AdminUserErasureBatchView.model_validate(payload)

    assert view.status.value == "PREVIEWED"
    assert view.requiredConfirmation == "ERASE 1 USER"
    assert view.summary.model_dump() == {
        "requested": 1,
        "ready": 1,
        "alreadyInProgress": 0,
        "alreadyCompleted": 0,
        "notFound": 0,
    }
    assert view.results[0].status.value == "READY"
    assert view.results[0].requestId is None


def test_batch_view_validates_actual_scrubbed_status_service_payload(monkeypatch):
    monkeypatch.setenv("USER_ERASURE_ENABLED", "false")
    batchId = "414c6f2b-a630-49c2-89eb-444514479384"
    repository = _BatchModelRepository({
        "id": batchId,
        "status": "IN_PROGRESS",
        "expires_at": "2026-08-26T10:15:00+00:00",
        "items": [{
            "id": "item-1",
            "target_user_id": None,
            "classification": "READY",
            "request_id": "request-1",
            "request_status": "COMPLETED",
            "error_code": None,
        }],
    })
    service = UserErasureBatchService(repository=repository)

    payload = service.getStatus(
        batchId, SimpleNamespace(adminId="admin-1")
    )
    view = AdminUserErasureBatchView.model_validate(payload)

    assert view.status.value == "COMPLETED"
    assert view.requiredConfirmation is None
    assert view.results[0].userId is None
    assert view.results[0].requestId == "request-1"
    assert view.results[0].status.value == "COMPLETED"


def test_access_batch_result_and_summary_use_spec_contract():
    result = AdminUserAccessBatchResponse.model_validate({
        "status": "COMPLETED",
        "summary": {"requested": 1, "updated": 1, "failed": 0, "withWarnings": 0},
        "results": [{
            "userId": "user-1", "outcome": "UPDATED", "isBanned": True,
            "bannedAt": "2026-08-26T00:00:00Z", "bannedBy": "admin-1",
            "banReason": "support", "sessionsRevoked": 2,
            "supabaseAuthSynced": True, "warnings": [], "errorCode": None,
        }],
    })
    assert result.summary.updated == 1
    assert result.results[0].outcome == "UPDATED"

    with pytest.raises(ValidationError):
        AdminUserAccessBatchResponse.model_validate({
            **result.model_dump(),
            "results": [{**result.results[0].model_dump(), "outcome": "BANNED"}],
        })
