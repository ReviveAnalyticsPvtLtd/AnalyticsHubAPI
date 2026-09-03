import os

import pytest
from fastapi import HTTPException

from api.visitModels import WebsiteVisitRequest
from api.services.websiteVisitService import WebsiteVisitService


VALID_SESSION_ID = "c72c037f-244d-4ac6-9b48-59c33c0329a6"


class RecordingRepository:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def recordVisit(self, sessionId, path, userAgent, ipAddress):
        self.calls.append((sessionId, path, userAgent, ipAddress))
        if self.failure:
            raise self.failure


class RecordingRedis:
    def __init__(self, result=1, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    def eval(self, script, keyCount, key, limit, window):
        self.calls.append((script, keyCount, key, limit, window))
        if self.failure:
            raise self.failure
        return self.result


def payload():
    return WebsiteVisitRequest(sessionId=VALID_SESSION_ID, path="/pricing")


def test_track_visit_admits_then_persists_sanitized_metadata(monkeypatch):
    monkeypatch.setenv("VISIT_TRACKING_RATE_LIMIT_PER_MINUTE", "3")
    repository = RecordingRepository()
    redisClient = RecordingRedis()

    result = WebsiteVisitService(repository=repository, redisClient=redisClient).trackVisit(
        payload(), "browser/1.0", "2001:db8::1"
    )

    assert result == {"success": True}
    assert repository.calls == [
        (VALID_SESSION_ID, "/pricing", "browser/1.0", "2001:db8::1")
    ]
    assert redisClient.calls[0][1] == 1
    assert redisClient.calls[0][3:] == (3, 60)
    assert "2001:db8::1" not in redisClient.calls[0][2]


def test_track_visit_accepts_ipv4_and_discards_invalid_peer_metadata():
    repository = RecordingRepository()
    service = WebsiteVisitService(repository=repository, redisClient=RecordingRedis())

    service.trackVisit(payload(), "x" * 1025, "not-an-ip")
    service.trackVisit(payload(), None, "127.0.0.1")

    assert repository.calls[0][2:] == ("x" * 1024, None)
    assert repository.calls[1][2:] == (None, "127.0.0.1")


def test_track_visit_rejects_when_fixed_window_limit_is_exhausted():
    repository = RecordingRepository()
    service = WebsiteVisitService(repository=repository, redisClient=RecordingRedis(result=0))

    with pytest.raises(HTTPException) as excInfo:
        service.trackVisit(payload(), None, "127.0.0.1")

    assert excInfo.value.status_code == 429
    assert excInfo.value.detail == "Too many visit tracking requests. Try again later"
    assert repository.calls == []


def test_track_visit_returns_sanitized_unavailable_error_when_limiter_fails():
    repository = RecordingRepository()
    service = WebsiteVisitService(
        repository=repository,
        redisClient=RecordingRedis(failure=RuntimeError("redis://secret")),
    )

    with pytest.raises(HTTPException) as excInfo:
        service.trackVisit(payload(), None, "127.0.0.1")

    assert excInfo.value.status_code == 503
    assert excInfo.value.detail == "Visit tracking is temporarily unavailable"
    assert repository.calls == []


def test_track_visit_returns_sanitized_unavailable_error_when_persistence_fails():
    service = WebsiteVisitService(
        repository=RecordingRepository(failure=RuntimeError("postgres password=secret")),
        redisClient=RecordingRedis(),
    )

    with pytest.raises(HTTPException) as excInfo:
        service.trackVisit(payload(), None, "127.0.0.1")

    assert excInfo.value.status_code == 503
    assert excInfo.value.detail == "Visit tracking is temporarily unavailable"
