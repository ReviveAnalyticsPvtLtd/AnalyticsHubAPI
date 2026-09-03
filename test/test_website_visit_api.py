import os

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routers import tracking
from api.services.websiteVisitService import getWebsiteVisitService


VALID_SESSION_ID = "c72c037f-244d-4ac6-9b48-59c33c0329a6"


class RecordingService:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def trackVisit(self, payload, userAgent, ipAddress):
        self.calls.append((payload, userAgent, ipAddress))
        if self.failure:
            raise self.failure
        return {"success": True}


@pytest.fixture()
def client():
    service = RecordingService()
    app = FastAPI()
    app.include_router(tracking.router, prefix="/track")
    app.dependency_overrides[getWebsiteVisitService] = lambda: service
    testClient = TestClient(app, raise_server_exceptions=False)
    try:
        yield testClient, service
    finally:
        testClient.close()
        app.dependency_overrides.clear()


def test_public_visit_route_is_mounted_and_persists_only_direct_peer_metadata(client):
    testClient, service = client

    response = testClient.post(
        "/track/visit",
        json={"sessionId": VALID_SESSION_ID, "path": "/"},
        headers={"user-agent": "test-browser", "x-forwarded-for": "203.0.113.9"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert service.calls[0][1] == "test-browser"
    assert service.calls[0][2] != "203.0.113.9"


def test_public_visit_route_keeps_standard_validation_shape_without_echoing_payload(client):
    testClient, _service = client

    response = testClient.post(
        "/track/visit",
        json={"sessionId": VALID_SESSION_ID, "path": "//unsafe", "secret": "do-not-echo"},
    )

    assert response.status_code == 422
    assert "detail" in response.json()
    assert VALID_SESSION_ID not in response.text
    assert "do-not-echo" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("status", [429, 503])
def test_public_visit_route_preserves_sanitized_admission_and_persistence_errors(status, client):
    testClient, service = client
    service.failure = HTTPException(status_code=status, detail="safe error")

    response = testClient.post(
        "/track/visit", json={"sessionId": VALID_SESSION_ID, "path": "/"}
    )

    assert response.status_code == status
    assert response.json() == {"detail": "safe error"}
