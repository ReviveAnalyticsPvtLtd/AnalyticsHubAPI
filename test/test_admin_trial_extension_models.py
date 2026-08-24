import pytest
from pydantic import ValidationError

from api.adminModels import (
    AdminFreeTrialExtensionRequest,
    AdminFreeTrialExtensionResponse,
)


def test_trial_extension_request_accepts_one_or_many_users_and_deduplicates():
    payload = AdminFreeTrialExtensionRequest.model_validate({
        "userIds": [" user-1 ", "user-2", "user-1"],
        "days": 5,
        "reason": "  Support extension  ",
    })

    assert payload.userIds == ["user-1", "user-2"]
    assert payload.days == 5
    assert payload.reason == "Support extension"


@pytest.mark.parametrize("days", [0, 31, -1, 1.5, True, "5"])
def test_trial_extension_request_rejects_days_outside_strict_1_to_30(days):
    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionRequest.model_validate({
            "userIds": ["user-1"],
            "days": days,
        })


def test_trial_extension_request_rejects_empty_or_more_than_100_users():
    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionRequest.model_validate({"userIds": [], "days": 1})

    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionRequest.model_validate({
            "userIds": [f"user-{index}" for index in range(101)],
            "days": 1,
        })


def test_trial_extension_request_normalizes_blank_reason_to_none():
    payload = AdminFreeTrialExtensionRequest.model_validate({
        "userIds": ["user-1"],
        "days": 30,
        "reason": "   ",
    })
    assert payload.reason is None


def test_trial_extension_request_bounds_each_user_identifier():
    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionRequest.model_validate({
            "userIds": ["x" * 129],
            "days": 5,
        })


def test_trial_extension_response_is_strict_and_sanitized():
    response = AdminFreeTrialExtensionResponse.model_validate({
        "batchId": "batch-1",
        "status": "PARTIAL_SUCCESS",
        "days": 5,
        "summary": {
            "requested": 2,
            "extended": 1,
            "failed": 1,
            "creditSyncPending": 0,
        },
        "results": [
            {
                "userId": "user-1",
                "outcome": "EXTENDED",
                "daysAdded": 5,
                "previousExpiry": "2026-09-01T00:00:00+00:00",
                "newExpiry": "2026-09-06T00:00:00+00:00",
                "creditsRefreshed": True,
                "creditSyncStatus": "SYNCED",
                "accessStillBanned": False,
                "errorCode": None,
            },
            {
                "userId": "paid-user",
                "outcome": "FAILED",
                "daysAdded": None,
                "previousExpiry": None,
                "newExpiry": None,
                "creditsRefreshed": False,
                "creditSyncStatus": "NOT_APPLICABLE",
                "accessStillBanned": False,
                "errorCode": "PAID_SUBSCRIPTION_NOT_ELIGIBLE",
            },
        ],
    })
    assert response.summary.extended == 1

    with pytest.raises(ValidationError):
        AdminFreeTrialExtensionResponse.model_validate({
            **response.model_dump(),
            "rawSubscription": {"razorpay_token_id": "secret"},
        })
