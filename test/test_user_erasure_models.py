import pytest
from pydantic import ValidationError


def _models():
    from api.adminModels import (
        AdminUserErasureAcceptedView,
        AdminUserErasureRequest,
        AdminUserErasureStatus,
        AdminUserErasureStatusView,
        AdminUserErasureStepStatus,
    )

    return {
        "accepted": AdminUserErasureAcceptedView,
        "request": AdminUserErasureRequest,
        "requestStatus": AdminUserErasureStatus,
        "status": AdminUserErasureStatusView,
        "stepStatus": AdminUserErasureStepStatus,
    }


def test_erasure_request_requires_literal_confirmation_and_normalizes_reason():
    requestModel = _models()["request"]

    omitted = requestModel.model_validate({"confirmation": "ERASE"})
    blank = requestModel.model_validate({
        "confirmation": "ERASE",
        "reason": "   ",
    })
    trimmed = requestModel.model_validate({
        "confirmation": "ERASE",
        "reason": "  Customer request  ",
    })

    assert omitted.reason is None
    assert blank.reason is None
    assert trimmed.reason == "Customer request"

    for payload in (
        {"confirmation": "erase"},
        {"confirmation": "ERASE", "reason": "x" * 1001},
        {"confirmation": "ERASE", "unexpected": True},
    ):
        with pytest.raises(ValidationError):
            requestModel.model_validate(payload)


def test_erasure_status_views_allow_only_sanitized_operational_fields():
    models = _models()

    accepted = models["accepted"].model_validate({
        "requestId": "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        "userId": "user-1",
        "status": "PENDING",
        "createdAt": "2026-08-24T10:00:00+00:00",
    })
    status = models["status"].model_validate({
        "requestId": "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        "status": "PARTIALLY_FAILED",
        "createdAt": "2026-08-24T10:00:00+00:00",
        "startedAt": "2026-08-24T10:00:02+00:00",
        "completedAt": None,
        "lastErrorCode": "STORAGE_DELETE_FAILED",
        "steps": [
            {
                "name": "delete_storage",
                "status": "FAILED",
                "attempts": 5,
                "lastErrorCode": "STORAGE_DELETE_FAILED",
            }
        ],
    })

    assert accepted.status == models["requestStatus"].PENDING
    assert status.steps[0].status == models["stepStatus"].FAILED
    assert set(status.model_dump()) == {
        "requestId",
        "status",
        "createdAt",
        "startedAt",
        "completedAt",
        "lastErrorCode",
        "steps",
    }

    for forbidden in ("targetUserId", "reason", "resourceManifest", "rawError"):
        with pytest.raises(ValidationError):
            models["status"].model_validate({
                **status.model_dump(),
                forbidden: "must-not-be-exposed",
            })


def test_erasure_status_views_reject_unknown_statuses_and_negative_attempts():
    statusModel = _models()["status"]
    base = {
        "requestId": "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        "status": "PENDING",
        "createdAt": "2026-08-24T10:00:00+00:00",
        "startedAt": None,
        "completedAt": None,
        "lastErrorCode": None,
        "steps": [],
    }

    with pytest.raises(ValidationError):
        statusModel.model_validate({**base, "status": "CANCELLED"})

    with pytest.raises(ValidationError):
        statusModel.model_validate({
            **base,
            "steps": [{
                "name": "inventory",
                "status": "FAILED",
                "attempts": -1,
                "lastErrorCode": None,
            }],
        })
