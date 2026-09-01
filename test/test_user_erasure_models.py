import pytest
from pydantic import ValidationError


def _models():
    from api.adminModels import (
        AdminUserErasureAcceptedView,
        AdminUserErasureRequest,
        AdminUserErasureStatus,
    )

    return {
        "accepted": AdminUserErasureAcceptedView,
        "request": AdminUserErasureRequest,
        "requestStatus": AdminUserErasureStatus,
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


def test_erasure_accepted_view_allows_only_command_receipt_fields():
    models = _models()
    accepted = models["accepted"].model_validate({
        "requestId": "8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        "userId": "user-1",
        "status": "PENDING",
        "createdAt": "2026-09-01T10:00:00+00:00",
    })
    assert accepted.status == models["requestStatus"].PENDING
    assert set(accepted.model_dump()) == {
        "requestId", "userId", "status", "createdAt"
    }
    with pytest.raises(ValidationError):
        models["accepted"].model_validate({
            **accepted.model_dump(),
            "steps": [],
        })
