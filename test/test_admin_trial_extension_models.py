import pytest
from pydantic import ValidationError

from api.adminModels import (
    AdminFreeTrialExtensionRequest,
    AdminFreeTrialExtensionResponse,
)


def test_trial_extension_request_accepts_exactly_one_normalized_user():
    payload = AdminFreeTrialExtensionRequest.model_validate({
        "userId": " user-1 ",
        "days": 5,
        "reason": "  Support extension  ",
    })

    assert payload.userId == "user-1"
    assert payload.days == 5
    assert payload.reason == "Support extension"


@pytest.mark.parametrize("days", [0, 31, -1, 1.5, True, "5"])
def test_trial_extension_request_rejects_days_outside_strict_1_to_30(days):
    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionRequest.model_validate({
            "userId": "user-1",
            "days": days,
        })


@pytest.mark.parametrize("user_id", ["", "   ", "x" * 129])
def test_trial_extension_request_rejects_invalid_user_identifier(user_id):
    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionRequest.model_validate({
            "userId": user_id,
            "days": 5,
        })


def test_trial_extension_request_rejects_batch_shape():
    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionRequest.model_validate({
            "userIds": ["user-1", "user-2"],
            "days": 5,
        })


def test_trial_extension_request_normalizes_blank_reason_to_none():
    payload = AdminFreeTrialExtensionRequest.model_validate({
        "userId": "user-1",
        "days": 30,
        "reason": "   ",
    })
    assert payload.reason is None


def test_trial_extension_response_returns_one_sanitized_result():
    response = AdminFreeTrialExtensionResponse.model_validate({
        "extensionId": "extension-1",
        "userId": "user-1",
        "outcome": "EXTENDED",
        "daysAdded": 5,
        "previousExpiry": "2026-09-01T00:00:00+00:00",
        "newExpiry": "2026-09-06T00:00:00+00:00",
        "creditsRefreshed": True,
        "creditSyncStatus": "SYNCED",
        "accessStillBanned": False,
        "errorCode": None,
    })

    assert response.extensionId == "extension-1"
    assert response.outcome == "EXTENDED"

    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionResponse.model_validate({
            **response.model_dump(),
            "rawSubscription": {"razorpay_token_id": "secret"},
        })
