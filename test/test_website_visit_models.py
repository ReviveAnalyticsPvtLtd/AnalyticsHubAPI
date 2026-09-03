import uuid

import pytest
from pydantic import ValidationError

from api.visitModels import WebsiteVisitRequest, WebsiteVisitResponse


VALID_SESSION_ID = "c72c037f-244d-4ac6-9b48-59c33c0329a6"


def test_valid_visit_payload_preserves_uuid_and_relative_path():
    payload = WebsiteVisitRequest(sessionId=VALID_SESSION_ID, path="/")

    assert payload.sessionId == uuid.UUID(VALID_SESSION_ID)
    assert payload.path == "/"


@pytest.mark.parametrize("payload", [
    {"sessionId": "not-a-uuid", "path": "/"},
    {"sessionId": VALID_SESSION_ID, "path": ""},
    {"sessionId": VALID_SESSION_ID, "path": "relative"},
    {"sessionId": VALID_SESSION_ID, "path": "//other.example/path"},
    {"sessionId": VALID_SESSION_ID, "path": "https://other.example/path"},
    {"sessionId": VALID_SESSION_ID, "path": "/about?source=ad"},
    {"sessionId": VALID_SESSION_ID, "path": "/about#team"},
    {"sessionId": VALID_SESSION_ID, "path": "/bad\\path"},
    {"sessionId": VALID_SESSION_ID, "path": "/bad\x00path"},
    {"sessionId": VALID_SESSION_ID, "path": "/" + "x" * 2048},
    {"sessionId": VALID_SESSION_ID, "path": "/", "extra": "no"},
])
def test_visit_payload_rejects_invalid_or_unsafe_contract_values(payload):
    with pytest.raises(ValidationError):
        WebsiteVisitRequest(**payload)


def test_success_response_allows_only_a_true_success_flag():
    assert WebsiteVisitResponse(success=True).model_dump() == {"success": True}

    with pytest.raises(ValidationError):
        WebsiteVisitResponse(success=False)
