"""Admission and synchronous persistence for public website visit reports."""

import hashlib
import ipaddress
import os

import redis
from fastapi import HTTPException

from api.services.websiteVisitRepository import (
    WebsiteVisitRepository,
    getWebsiteVisitRepository,
)


VISIT_TRACKING_WINDOW_SECONDS = 60
DEFAULT_VISIT_TRACKING_RATE_LIMIT = 120
_VISIT_TRACKING_ADMIT_SCRIPT = """-- website-visit-admit
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if count > tonumber(ARGV[1]) then
    redis.call('DECR', KEYS[1])
    return 0
end
return 1
"""


def _rateLimit() -> int:
    try:
        return max(1, int(os.environ.get(
            "VISIT_TRACKING_RATE_LIMIT_PER_MINUTE",
            DEFAULT_VISIT_TRACKING_RATE_LIMIT,
        )))
    except (TypeError, ValueError):
        return DEFAULT_VISIT_TRACKING_RATE_LIMIT


def _validIpAddress(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


class WebsiteVisitService:
    def __init__(self, repository=None, redisClient=None):
        self._repository = repository
        self._redisClient = redisClient

    @property
    def repository(self) -> WebsiteVisitRepository:
        if self._repository is None:
            self._repository = getWebsiteVisitRepository()
        return self._repository

    @property
    def redisClient(self):
        if self._redisClient is None:
            self._redisClient = redis.Redis(
                host=os.environ.get("REDIS_HOST", "localhost"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD"),
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._redisClient

    def trackVisit(self, payload, userAgent: str | None, ipAddress: str | None) -> dict:
        validIpAddress = _validIpAddress(ipAddress)
        identity = validIpAddress or "unknown"
        rateKey = "website-visit-rate:" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
        try:
            admitted = self.redisClient.eval(
                _VISIT_TRACKING_ADMIT_SCRIPT,
                1,
                rateKey,
                _rateLimit(),
                VISIT_TRACKING_WINDOW_SECONDS,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Visit tracking is temporarily unavailable",
            ) from exc
        if not admitted:
            raise HTTPException(
                status_code=429,
                detail="Too many visit tracking requests. Try again later",
            )

        boundedUserAgent = userAgent[:1024] if userAgent else None
        try:
            self.repository.recordVisit(
                str(payload.sessionId),
                payload.path,
                boundedUserAgent,
                validIpAddress,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Visit tracking is temporarily unavailable",
            ) from exc
        return {"success": True}


_websiteVisitService: WebsiteVisitService | None = None


def getWebsiteVisitService() -> WebsiteVisitService:
    global _websiteVisitService
    if _websiteVisitService is None:
        _websiteVisitService = WebsiteVisitService()
    return _websiteVisitService
