import hashlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from starlette.datastructures import Headers

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.adminErrors import AdminApiError
from api.services.adminAuthService import (
    AdminAuthService,
    resolveAdminClientIp,
    verifyAdmin,
    verifyAdminForLogout,
)


ADMIN_SECRET = "admin-only-secret-for-tests"
ADMIN_ID = "4fa8af6f-71f4-4b05-b26f-fc89ac72a371"
OTHER_ADMIN_ID = "130516b0-f229-4f37-98b6-6e0be8ff9dd4"
VALID_PASSWORD = "correct horse battery"
OTHER_PASSWORD = "another correct password"
FIXED_NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, current=FIXED_NOW):
        self.current = current

    def __call__(self):
        return self.current


class FakeQuery:
    def __init__(self, client, tableName):
        self.client = client
        self.tableName = tableName
        self.filters = []
        self.limitCount = None
        self.operation = "select"
        self.payload = None

    def select(self, _fields):
        self.operation = "select"
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, count):
        self.limitCount = count
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = dict(payload)
        return self

    def execute(self):
        if self.operation == "insert":
            row = dict(self.payload)
            self.client.rows[self.tableName].append(row)
            self.client.insertCalls.append((self.tableName, row))
            return SimpleNamespace(data=[dict(row)])

        matching = [
            row for row in self.client.rows[self.tableName]
            if all(row.get(field) == value for field, value in self.filters)
        ]
        if self.limitCount is not None:
            matching = matching[:self.limitCount]
        if self.operation == "update":
            for row in matching:
                row.update(self.payload)
            self.client.updateCalls.append(
                (self.tableName, dict(self.payload), tuple(self.filters))
            )
        return SimpleNamespace(data=[dict(row) for row in matching])


class FakeAdminClient:
    def __init__(self, adminUsers):
        self.rows = {"admin_users": adminUsers, "admin_sessions": []}
        self.insertCalls = []
        self.updateCalls = []

    def table(self, name):
        if name not in self.rows:
            raise AssertionError(f"Unexpected table: {name}")
        return FakeQuery(self, name)


class FakeRedis:
    def __init__(self):
        self.counters = {}
        self.calls = []
        self.raiseOnAccess = False

    def _guard(self):
        if self.raiseOnAccess:
            raise ConnectionError("redis unavailable; secret=must-not-be-logged")

    def get(self, key):
        self._guard()
        self.calls.append(("get", key))
        return self.counters.get(key)

    def incr(self, key):
        self._guard()
        self.calls.append(("incr", key))
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):
        self._guard()
        self.calls.append(("expire", key, seconds))
        return True


@pytest.fixture(scope="module")
def passwordHasher():
    return PasswordHash.recommended()


@pytest.fixture
def authFixture(monkeypatch, passwordHasher):
    monkeypatch.setenv("ADMIN_JWT_SECRET", ADMIN_SECRET)
    monkeypatch.setenv("SECRET_KEY", "product-secret-must-never-be-used")
    admins = [
        {
            "id": ADMIN_ID,
            "email": "admin@example.com",
            "name": "Primary Admin",
            "password_hash": passwordHasher.hash(VALID_PASSWORD),
            "is_active": True,
            "last_login_at": None,
        },
        {
            "id": OTHER_ADMIN_ID,
            "email": "other@example.com",
            "name": "Other Admin",
            "password_hash": passwordHasher.hash(OTHER_PASSWORD),
            "is_active": True,
            "last_login_at": None,
        },
    ]
    client = FakeAdminClient(admins)
    redisClient = FakeRedis()
    clock = FixedClock()
    service = AdminAuthService(
        client=client,
        passwordHasher=passwordHasher,
        redisClient=redisClient,
        nowProvider=clock,
    )
    return SimpleNamespace(
        service=service,
        client=client,
        redis=redisClient,
        clock=clock,
        admins=admins,
    )


def assertUnauthorized(call):
    with pytest.raises(AdminApiError) as captured:
        call()
    assert captured.value.statusCode == 401


def test_login_issues_eight_hour_admin_token_and_persists_only_digest(authFixture):
    response = authFixture.service.login(
        " ADMIN@example.com ", VALID_PASSWORD, "203.0.113.10"
    )

    payload = jwt.decode(
        response["token"],
        ADMIN_SECRET,
        algorithms=["HS256"],
        audience="nubrix-admin-api",
        issuer="nubrix-admin",
    )
    assert payload["type"] == "admin"
    assert payload["sub"] == ADMIN_ID
    assert uuid.UUID(payload["jti"])
    assert payload["exp"] - payload["iat"] == 8 * 60 * 60
    assert response["admin"] == {
        "id": ADMIN_ID,
        "email": "admin@example.com",
        "name": "Primary Admin",
    }
    session = authFixture.client.rows["admin_sessions"][0]
    assert session["token_hash"] == hashlib.sha256(
        response["token"].encode("utf-8")
    ).hexdigest()
    assert response["token"] not in repr(authFixture.client.rows)


def test_invalid_inactive_and_unknown_credentials_share_one_response(authFixture):
    authFixture.admins[1]["is_active"] = False
    cases = [
        ("admin@example.com", "wrong-password"),
        ("other@example.com", OTHER_PASSWORD),
        ("missing@example.com", "irrelevant-password"),
        ("not-an-email", "irrelevant-password"),
    ]

    for email, password in cases:
        with pytest.raises(AdminApiError) as captured:
            authFixture.service.login(email, password, "203.0.113.10")
        assert captured.value.statusCode == 401
        assert captured.value.message == "Invalid credentials"


def test_unknown_user_dummy_hash_is_created_once_per_service(authFixture):
    assert authFixture.service._dummyPasswordHash.startswith("$argon2id$")
    with patch.object(
        authFixture.service.passwordHasher,
        "hash",
        wraps=authFixture.service.passwordHasher.hash,
    ) as hashPassword:
        authFixture.service.login(
            "admin@example.com", VALID_PASSWORD, "198.51.100.9"
        )
        for email in ("one@example.com", "two@example.com"):
            with pytest.raises(AdminApiError):
                authFixture.service.login(email, "wrong-password", "198.51.100.10")

    assert hashPassword.call_count == 0


def test_login_rehashes_and_persists_an_outdated_password(monkeypatch):
    monkeypatch.setenv("ADMIN_JWT_SECRET", ADMIN_SECRET)
    oldHasher = PasswordHash((
        Argon2Hasher(time_cost=1, memory_cost=8192, parallelism=1),
    ))
    oldHash = oldHasher.hash(VALID_PASSWORD)
    admin = {
        "id": ADMIN_ID,
        "email": "admin@example.com",
        "name": "Primary Admin",
        "password_hash": oldHash,
        "is_active": True,
    }
    client = FakeAdminClient([admin])
    recommended = PasswordHash.recommended()
    service = AdminAuthService(
        client=client,
        passwordHasher=recommended,
        redisClient=FakeRedis(),
        nowProvider=FixedClock(),
    )

    service.login("admin@example.com", VALID_PASSWORD, "203.0.113.10")

    replacement = admin["password_hash"]
    assert replacement != oldHash
    assert recommended.verify(VALID_PASSWORD, replacement)


def test_verify_rejects_product_expired_revoked_and_hash_mismatch_tokens(authFixture):
    productToken = jwt.encode(
        {
            "sub": ADMIN_ID,
            "jti": str(uuid.uuid4()),
            "type": "product",
            "iss": "nubrix-admin",
            "aud": "nubrix-admin-api",
            "iat": int(FIXED_NOW.timestamp()),
            "exp": int((FIXED_NOW + timedelta(hours=1)).timestamp()),
        },
        ADMIN_SECRET,
        algorithm="HS256",
    )
    expiredToken = jwt.encode(
        {
            "sub": ADMIN_ID,
            "jti": str(uuid.uuid4()),
            "type": "admin",
            "iss": "nubrix-admin",
            "aud": "nubrix-admin-api",
            "iat": int((FIXED_NOW - timedelta(hours=9)).timestamp()),
            "exp": int((FIXED_NOW - timedelta(hours=1)).timestamp()),
        },
        ADMIN_SECRET,
        algorithm="HS256",
    )
    revoked = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    authFixture.client.rows["admin_sessions"][-1]["revoked_at"] = FIXED_NOW.isoformat()
    mismatched = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    authFixture.client.rows["admin_sessions"][-1]["token_hash"] = "0" * 64

    for token in (productToken, expiredToken, revoked["token"], mismatched["token"]):
        assertUnauthorized(lambda token=token: authFixture.service.verifyToken(token))


def test_verify_requires_matching_live_session_and_active_admin(authFixture):
    missingSession = jwt.encode(
        {
            "sub": ADMIN_ID,
            "jti": str(uuid.uuid4()),
            "type": "admin",
            "iss": "nubrix-admin",
            "aud": "nubrix-admin-api",
            "iat": int(FIXED_NOW.timestamp()),
            "exp": int((FIXED_NOW + timedelta(hours=8)).timestamp()),
        },
        ADMIN_SECRET,
        algorithm="HS256",
    )
    assertUnauthorized(lambda: authFixture.service.verifyToken(missingSession))

    login = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    authFixture.client.rows["admin_sessions"][-1]["expires_at"] = (
        FIXED_NOW - timedelta(seconds=1)
    ).isoformat()
    assertUnauthorized(lambda: authFixture.service.verifyToken(login["token"]))

    activeLogin = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    authFixture.admins[0]["is_active"] = False
    assertUnauthorized(lambda: authFixture.service.verifyToken(activeLogin["token"]))


def test_logout_is_idempotent_and_leaves_other_session_active(authFixture):
    first = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    second = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    firstContext = authFixture.service.verifyToken(
        first["token"], allowRevoked=True, requireActiveAdmin=False
    )

    assert authFixture.service.logout(firstContext) == {"success": True}
    revokedContext = authFixture.service.verifyToken(
        first["token"], allowRevoked=True, requireActiveAdmin=False
    )
    assert authFixture.service.logout(revokedContext) == {"success": True}

    assertUnauthorized(lambda: authFixture.service.verifyToken(first["token"]))
    assert authFixture.service.verifyToken(second["token"]).adminId == ADMIN_ID
    assert authFixture.client.rows["admin_sessions"][1]["revoked_at"] is None


def test_verify_suppresses_last_used_writes_for_five_minutes(authFixture):
    response = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    authFixture.client.updateCalls.clear()

    authFixture.service.verifyToken(response["token"])
    authFixture.clock.current += timedelta(minutes=4, seconds=59)
    authFixture.service.verifyToken(response["token"])
    assert authFixture.client.updateCalls == []

    authFixture.clock.current += timedelta(seconds=1)
    authFixture.service.verifyToken(response["token"])
    sessionUpdates = [
        call for call in authFixture.client.updateCalls if call[0] == "admin_sessions"
    ]
    assert len(sessionUpdates) == 1
    assert sessionUpdates[0][1] == {
        "last_used_at": authFixture.clock.current.isoformat()
    }


def test_throttle_limits_email_and_ip_and_redis_failure_fails_open(authFixture):
    for offset in range(5):
        with pytest.raises(AdminApiError):
            authFixture.service.login(
                "admin@example.com", "wrong-password", f"203.0.113.{offset + 1}"
            )
    with pytest.raises(AdminApiError) as emailLimited:
        authFixture.service.login(
            "admin@example.com", VALID_PASSWORD, "203.0.113.99"
        )
    assert emailLimited.value.statusCode == 429

    ip = "198.51.100.40"
    for offset in range(20):
        with pytest.raises(AdminApiError):
            authFixture.service.login(
                f"unknown-{offset}@example.com", "wrong-password", ip
            )
    with pytest.raises(AdminApiError) as ipLimited:
        authFixture.service.login("other@example.com", OTHER_PASSWORD, ip)
    assert ipLimited.value.statusCode == 429

    authFixture.redis.raiseOnAccess = True
    assert authFixture.service.login(
        "other@example.com", OTHER_PASSWORD, "192.0.2.22"
    )["token"]


def test_throttle_uses_expiring_redacted_keys_and_logs_no_identifiers(authFixture):
    email = "missing@example.com"
    ip = "203.0.113.200"
    password = "private-wrong-password"
    with pytest.raises(AdminApiError):
        authFixture.service.login(email, password, ip)

    incrementedKeys = [call[1] for call in authFixture.redis.calls if call[0] == "incr"]
    expiredKeys = [call[1] for call in authFixture.redis.calls if call[0] == "expire"]
    expiryWindows = [call[2] for call in authFixture.redis.calls if call[0] == "expire"]
    assert len(incrementedKeys) == 2
    assert set(expiredKeys) == set(incrementedKeys)
    assert expiryWindows == [15 * 60, 15 * 60]
    assert all(email not in key and ip not in key for key in incrementedKeys)

    authFixture.redis.raiseOnAccess = True
    with patch("api.services.adminAuthService.logger.warning") as warning:
        with pytest.raises(AdminApiError):
            authFixture.service.login(email, password, ip)
    loggedArguments = repr(warning.call_args_list)
    assert email not in loggedArguments
    assert ip not in loggedArguments
    assert password not in loggedArguments
    assert "must-not-be-logged" not in loggedArguments


def test_client_ip_honors_forwarding_only_from_configured_trusted_proxy(monkeypatch):
    monkeypatch.setenv("ADMIN_TRUSTED_PROXY_IPS", "10.0.0.8, 2001:db8::8")
    trustedRequest = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.8"),
        headers=Headers({
            "x-forwarded-for": "unknown, not-an-ip, 198.51.100.44, 10.0.0.8"
        }),
    )
    untrustedRequest = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.9"),
        headers=Headers({"x-forwarded-for": "198.51.100.99"}),
    )

    assert resolveAdminClientIp(trustedRequest) == "198.51.100.44"
    assert resolveAdminClientIp(untrustedRequest) == "203.0.113.9"


def test_fastapi_dependencies_use_injected_admin_service(authFixture):
    response = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=response["token"]
    )

    context = verifyAdmin(credentials, authFixture.service)
    assert context.adminId == ADMIN_ID
    authFixture.service.logout(context)
    logoutContext = verifyAdminForLogout(credentials, authFixture.service)
    assert logoutContext.sessionId == context.sessionId
    assertUnauthorized(lambda: verifyAdmin(None, authFixture.service))


def test_admin_secret_never_falls_back_to_product_secret(authFixture, monkeypatch):
    monkeypatch.delenv("ADMIN_JWT_SECRET")
    with patch("api.services.adminAuthService.logger.error") as errorLog:
        with pytest.raises(AdminApiError) as missingSecret:
            authFixture.service.login(
                "admin@example.com", VALID_PASSWORD, "203.0.113.10"
            )

    assert missingSecret.value.statusCode == 500
    assert missingSecret.value.message == "Admin authentication is unavailable"
    loggedArguments = repr(errorLog.call_args_list)
    assert VALID_PASSWORD not in loggedArguments
    assert "admin@example.com" not in loggedArguments
    assert "203.0.113.10" not in loggedArguments
