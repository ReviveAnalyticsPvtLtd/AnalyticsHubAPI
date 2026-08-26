import os
import uuid

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "")

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from api.adminErrors import AdminApiError
from api.routers import admin
from api.services.adminAuthService import AdminContext, verifyAdmin
from api.services.userErasureBatchService import getUserErasureBatchService
from main import admin_exception_handler, request_validation_handler


BATCH_ID = "b0eb9203-836a-41da-b33c-e46bcecc12c3"
KEY = "8cfdb150-417d-47ab-acd1-fef39d2bc14e"
ADMIN = AdminContext(
    adminId="admin-1",
    email="admin@example.com",
    name="Admin",
    sessionId="session-1",
    token="admin-token",
)


def _batch(status="PREVIEWED", userId="user-1"):
    return {
        "batchId": BATCH_ID,
        "status": status,
        "expiresAt": "2026-08-26T12:15:00+00:00",
        "requiredConfirmation": (
            "ERASE 1 USER" if status == "PREVIEWED" else None
        ),
        "summary": {
            "requested": 1,
            "ready": 1,
            "alreadyInProgress": 0,
            "alreadyCompleted": 0,
            "notFound": 0,
        },
        "results": [{
            "itemId": "item-1",
            "userId": userId,
            "status": "READY" if status == "PREVIEWED" else "PENDING",
            "requestId": None if status == "PREVIEWED" else "request-1",
            "errorCode": None,
        }],
    }


class FakeBatchService:
    calls = []

    def preview(self, payload, idempotencyKey, adminContext):
        FakeBatchService.calls.append(
            ("preview", payload, idempotencyKey, adminContext)
        )
        try:
            uuid.UUID(idempotencyKey)
        except (ValueError, TypeError, AttributeError) as exc:
            raise AdminApiError(422, "Invalid Idempotency-Key header") from exc
        return _batch()

    def confirm(self, batchId, payload, adminContext):
        FakeBatchService.calls.append(
            ("confirm", batchId, payload, adminContext)
        )
        self._raiseFor(batchId)
        return _batch("IN_PROGRESS")

    def getStatus(self, batchId, adminContext):
        FakeBatchService.calls.append(("status", batchId, adminContext))
        self._raiseFor(batchId)
        return _batch("IN_PROGRESS", userId=None)

    @staticmethod
    def _raiseFor(batchId):
        errors = {
            "forbidden": (403, "Only the preview creator can access this batch"),
            "missing": (404, "User erasure batch not found"),
            "conflict": (409, "User erasure batch preview has expired"),
            "failed": (500, "Failed to read user erasure batch"),
        }
        if batchId in errors:
            statusCode, message = errors[batchId]
            raise AdminApiError(statusCode, message)


async def _verifyAdmin(request: Request):
    if request.headers.get("Authorization") != "Bearer admin-token":
        raise AdminApiError(401, "Authentication required")
    return ADMIN


@pytest.fixture()
def client():
    app = FastAPI(root_path="/api/latest")
    app.add_exception_handler(AdminApiError, admin_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.include_router(admin.router, prefix="/admin")
    app.dependency_overrides[verifyAdmin] = _verifyAdmin
    app.dependency_overrides[getUserErasureBatchService] = FakeBatchService
    FakeBatchService.calls = []
    with TestClient(app, raise_server_exceptions=False) as testClient:
        yield testClient


def _headers(**extra):
    return {"Authorization": "Bearer admin-token", **extra}


def test_preview_confirm_and_status_routes_use_strict_models(client):
    preview = client.post(
        "/admin/user-erasure-batches",
        headers=_headers(**{"Idempotency-Key": KEY}),
        json={"userIds": [" user-1 ", "user-1"], "reason": "  support  "},
    )
    assert preview.status_code == 201
    assert preview.json() == _batch()

    confirmed = client.post(
        f"/admin/user-erasure-batches/{BATCH_ID}/confirm",
        headers=_headers(),
        json={"confirmation": "ERASE 1 USER"},
    )
    assert confirmed.status_code == 202
    assert confirmed.json()["status"] == "IN_PROGRESS"

    status = client.get(
        f"/admin/user-erasure-batches/{BATCH_ID}", headers=_headers()
    )
    assert status.status_code == 200
    assert status.json()["results"][0]["userId"] is None

    previewCall = FakeBatchService.calls[0]
    assert previewCall[1].userIds == ["user-1"]
    assert previewCall[1].reason == "support"
    assert previewCall[2] == KEY
    assert previewCall[3] == ADMIN


@pytest.mark.parametrize(
    ("method", "path", "jsonBody"),
    [
        ("post", "/admin/user-erasure-batches", {"userIds": ["user-1"]}),
        (
            "post",
            f"/admin/user-erasure-batches/{BATCH_ID}/confirm",
            {"confirmation": "ERASE 1 USER"},
        ),
        ("get", f"/admin/user-erasure-batches/{BATCH_ID}", None),
    ],
)
def test_batch_routes_require_admin_authentication(client, method, path, jsonBody):
    response = client.request(method, path, json=jsonBody)
    assert response.status_code == 401
    assert response.json() == {"message": "Authentication required"}


def test_preview_requires_valid_idempotency_header(client):
    missing = client.post(
        "/admin/user-erasure-batches",
        headers=_headers(),
        json={"userIds": ["user-1"]},
    )
    malformed = client.post(
        "/admin/user-erasure-batches",
        headers=_headers(**{"Idempotency-Key": "not-a-uuid"}),
        json={"userIds": ["user-1"]},
    )
    assert missing.status_code == 422
    assert any(
        key.endswith("Idempotency-Key")
        for key in missing.json()["errors"]
    )
    assert malformed.status_code == 422
    assert malformed.json() == {"message": "Invalid Idempotency-Key header"}


@pytest.mark.parametrize(
    "body",
    [
        {"userIds": []},
        {"userIds": ["user-1"], "unexpected": True},
        {"userIds": [str(index) for index in range(26)]},
    ],
)
def test_preview_rejects_invalid_strict_bodies(client, body):
    response = client.post(
        "/admin/user-erasure-batches",
        headers=_headers(**{"Idempotency-Key": KEY}),
        json=body,
    )
    assert response.status_code == 422
    assert response.json()["message"] == "Validation failed"


def test_confirm_rejects_invalid_confirmation_body(client):
    response = client.post(
        f"/admin/user-erasure-batches/{BATCH_ID}/confirm",
        headers=_headers(),
        json={"confirmation": "ERASE"},
    )
    assert response.status_code == 422
    assert response.json()["message"] == "Validation failed"


@pytest.mark.parametrize("batchId,statusCode", [
    ("forbidden", 403),
    ("missing", 404),
    ("conflict", 409),
    ("failed", 500),
])
def test_status_flattens_safe_service_errors(client, batchId, statusCode):
    response = client.get(
        f"/admin/user-erasure-batches/{batchId}", headers=_headers()
    )
    assert response.status_code == statusCode
    assert set(response.json()) == {"message"}


def test_public_batch_response_excludes_internal_fields(client):
    response = client.get(
        f"/admin/user-erasure-batches/{BATCH_ID}", headers=_headers()
    )
    body = response.json()
    forbidden = {
        "request_hash", "subject_fingerprint", "requested_by", "reason",
        "admin_session_created_at", "last_error",
    }
    assert not forbidden.intersection(str(body))
    assert set(body) == {
        "batchId", "status", "expiresAt", "requiredConfirmation",
        "summary", "results",
    }
