import pytest
from pydantic import ValidationError

from api.adminModels import (
    AdminUserAccessBatchRequest,
    AdminUserAccessBatchResponse,
)


def test_access_batch_normalizes_ids_and_optional_reason():
    payload = AdminUserAccessBatchRequest.model_validate({
        "userIds": [" user-1 ", "user-1", "user-2"],
        "banned": True,
        "reason": "  support action  ",
    })

    assert payload.userIds == ["user-1", "user-2"]
    assert payload.reason == "support action"


@pytest.mark.parametrize(
    "user_ids",
    [[], [""], ["x" * 129], [str(index) for index in range(101)]],
)
def test_access_batch_rejects_invalid_user_ids(user_ids):
    with pytest.raises(ValidationError):
        AdminUserAccessBatchRequest.model_validate({
            "userIds": user_ids,
            "banned": True,
        })


def test_access_batch_result_and_summary_use_spec_contract():
    result = AdminUserAccessBatchResponse.model_validate({
        "status": "COMPLETED",
        "summary": {
            "requested": 1,
            "updated": 1,
            "failed": 0,
            "withWarnings": 0,
        },
        "results": [{
            "userId": "user-1",
            "outcome": "UPDATED",
            "isBanned": True,
            "bannedAt": "2026-08-26T00:00:00Z",
            "bannedBy": "admin-1",
            "banReason": "support",
            "sessionsRevoked": 2,
            "supabaseAuthSynced": True,
            "warnings": [],
            "errorCode": None,
        }],
    })

    assert result.summary.updated == 1
    assert result.results[0].outcome == "UPDATED"

    with pytest.raises(ValidationError):
        AdminUserAccessBatchResponse.model_validate({
            **result.model_dump(),
            "results": [{
                **result.results[0].model_dump(),
                "outcome": "BANNED",
            }],
        })
