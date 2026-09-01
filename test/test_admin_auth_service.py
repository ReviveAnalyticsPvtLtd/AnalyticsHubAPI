import hashlib
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
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


TEST_SECRET_KEY = "product-secret-for-tests"
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
        self.orderBy = None

    def select(self, fields):
        self.operation = "select"
        self.client.selectFields.append((self.tableName, fields))
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, count):
        self.limitCount = count
        return self

    def order(self, column, desc=False):
        self.orderBy = (column, desc)
        return self

    def is_(self, field, value):
        self.filters.append((field, None if value == "null" else value))
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
        if self.client.failureOperation == (self.tableName, self.operation):
            raise RuntimeError("backend unavailable; secret=must-not-be-logged")
        if self.operation == "insert":
            row = dict(self.payload)
            self.client.rows[self.tableName].append(row)
            self.client.insertCalls.append((self.tableName, row))
            return SimpleNamespace(data=[dict(row)])

        matching = [
            row for row in self.client.rows[self.tableName]
            if all(row.get(field) == value for field, value in self.filters)
        ]
        if self.orderBy is not None:
            column, descending = self.orderBy
            matching.sort(
                key=lambda row: str(row.get(column, "")), reverse=descending
            )
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
        self.selectFields = []
        self.failureOperation = None

    def table(self, name):
        if name not in self.rows:
            raise AssertionError(f"Unexpected table: {name}")
        return FakeQuery(self, name)


class FakeRedis:
    def __init__(self):
        self.counters = {}
        self.ttls = {}
        self.calls = []
        self.raiseOnAccess = False
        self.raiseOnDelete = False
        self.raiseOnRelease = False
        self.readBarrier = None
        self.readBarrierKey = None
        self._lock = threading.Lock()

    def _guard(self):
        if self.raiseOnAccess:
            raise ConnectionError("redis unavailable; secret=must-not-be-logged")

    def get(self, key):
        self._guard()
        self.calls.append(("get", key))
        value = self.counters.get(key)
        if self.readBarrier is not None and key == self.readBarrierKey:
            self.readBarrier.wait(timeout=5)
        return value

    def incr(self, key):
        self._guard()
        self.calls.append(("incr", key))
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):
        self._guard()
        self.calls.append(("expire", key, seconds))
        self.ttls[key] = seconds
        return True

    def delete(self, key):
        self._guard()
        if self.raiseOnDelete:
            raise ConnectionError("delete failed; secret=must-not-be-logged")
        self.calls.append(("delete", key))
        self.counters.pop(key, None)
        self.ttls.pop(key, None)
        return 1

    def eval(self, script, keyCount, key, *arguments):
        self._guard()
        assert keyCount == 1
        if self.raiseOnRelease and script.startswith("-- admin-login-release"):
            raise ConnectionError("release failed; secret=must-not-be-logged")
        with self._lock:
            self.calls.append(("eval", key, *arguments))
            if script.startswith("-- admin-login-admit"):
                limit, window = map(int, arguments)
                count = self.counters.get(key, 0) + 1
                self.counters[key] = count
                if self.ttls.get(key, -1) < 0:
                    self.ttls[key] = window
                if count > limit:
                    self.counters[key] -= 1
                    return 0
                return 1
            if script.startswith("-- admin-login-release"):
                window = int(arguments[0])
                count = self.counters.get(key, 0)
                if count <= 1:
                    self.counters.pop(key, None)
                    self.ttls.pop(key, None)
                else:
                    self.counters[key] = count - 1
                    if self.ttls.get(key, -1) < 0:
                        self.ttls[key] = window
                return 1
            raise AssertionError("Unexpected Lua script")


@pytest.fixture(scope="module")
def passwordHasher():
    return PasswordHash.recommended()


@pytest.fixture
def authFixture(monkeypatch, passwordHasher):
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET_KEY)
    admins = [
        {
            "id": ADMIN_ID,
            "email": "admin@example.com",
            "name": "Primary Admin",
            "password_hash": passwordHasher.hash(VALID_PASSWORD),
            "is_active": True,
            "last_login_at": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": OTHER_ADMIN_ID,
            "email": "other@example.com",
            "name": "Other Admin",
            "password_hash": passwordHasher.hash(OTHER_PASSWORD),
            "is_active": True,
            "last_login_at": None,
            "created_at": "2026-02-01T00:00:00+00:00",
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


def persistTokenSession(authFixture, claims):
    token = jwt.encode(claims, TEST_SECRET_KEY, algorithm="HS256")
    sessionId = claims.get("jti")
    if isinstance(sessionId, str):
        authFixture.client.rows["admin_sessions"].append({
            "id": sessionId,
            "admin_id": ADMIN_ID,
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "created_at": FIXED_NOW.isoformat(),
            "expires_at": (FIXED_NOW + timedelta(hours=8)).isoformat(),
            "revoked_at": None,
            "last_used_at": FIXED_NOW.isoformat(),
        })
    return token


def validClaims(**overrides):
    claims = {
        "sub": ADMIN_ID,
        "jti": str(uuid.uuid4()),
        "type": "admin",
        "iss": "nubrix-admin",
        "aud": "nubrix-admin-api",
        "iat": int(FIXED_NOW.timestamp()),
        "exp": int((FIXED_NOW + timedelta(hours=8)).timestamp()),
    }
    claims.update(overrides)
    return claims


def throttleKeys(email="admin@example.com", ip="203.0.113.10"):
    return (
        "admin:login:email:" + hashlib.sha256(email.encode("utf-8")).hexdigest(),
        "admin:login:ip:" + hashlib.sha256(ip.encode("utf-8")).hexdigest(),
    )


def assertThrottleReservationsReleased(authFixture, email="admin@example.com",
                                       ip="203.0.113.10"):
    emailKey, ipKey = throttleKeys(email, ip)
    assert emailKey not in authFixture.redis.counters
    assert ipKey not in authFixture.redis.counters


def test_login_issues_eight_hour_admin_token_and_persists_only_digest(authFixture):
    response = authFixture.service.login(
        " ADMIN@example.com ", VALID_PASSWORD, "203.0.113.10"
    )

    payload = jwt.decode(
        response["token"],
        TEST_SECRET_KEY,
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


def test_admin_tokens_always_use_product_secret(authFixture, monkeypatch):
    monkeypatch.setenv("ADMIN_JWT_SECRET", "unused-admin-secret")
    response = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )

    payload = jwt.decode(
        response["token"],
        TEST_SECRET_KEY,
        algorithms=["HS256"],
        audience="nubrix-admin-api",
        issuer="nubrix-admin",
    )

    assert payload["sub"] == ADMIN_ID


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
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET_KEY)
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


def test_successful_login_clears_only_the_email_failure_counter(authFixture):
    emailKey = "admin:login:email:" + hashlib.sha256(
        b"admin@example.com"
    ).hexdigest()
    ipKey = "admin:login:ip:" + hashlib.sha256(b"203.0.113.10").hexdigest()
    authFixture.redis.counters[emailKey] = 3
    authFixture.redis.counters[ipKey] = 3

    authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )

    assert emailKey not in authFixture.redis.counters
    assert authFixture.redis.counters[ipKey] == 3
    assert ("delete", emailKey) in authFixture.redis.calls


def test_successful_login_counter_cleanup_fails_open_with_redacted_log(authFixture):
    authFixture.redis.raiseOnDelete = True
    with patch("api.services.adminAuthService.logger.warning") as warning:
        response = authFixture.service.login(
            "admin@example.com", VALID_PASSWORD, "203.0.113.10"
        )

    assert response["token"]
    assert warning.call_count == 1
    loggedArguments = repr(warning.call_args_list)
    assert "admin@example.com" not in loggedArguments
    assert "203.0.113.10" not in loggedArguments
    assert VALID_PASSWORD not in loggedArguments
    assert "must-not-be-logged" not in loggedArguments


@pytest.mark.parametrize("missingClaim", ["sub", "jti", "type", "iss", "aud", "iat", "exp"])
def test_verify_requires_every_admin_claim(authFixture, missingClaim):
    claims = validClaims()
    del claims[missingClaim]
    token = persistTokenSession(authFixture, claims)

    assertUnauthorized(lambda: authFixture.service.verifyToken(token))


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": 7},
        {"jti": 7},
        {"type": ["admin"]},
        {"iss": ["nubrix-admin"]},
        {"iat": str(int(FIXED_NOW.timestamp()))},
        {"exp": float(int((FIXED_NOW + timedelta(hours=8)).timestamp()))},
    ],
)
def test_verify_rejects_malformed_admin_claim_types(authFixture, overrides):
    token = persistTokenSession(authFixture, validClaims(**overrides))
    assertUnauthorized(lambda: authFixture.service.verifyToken(token))


def test_verify_rejects_non_exact_lifetime_and_audience_list(authFixture):
    oneHourToken = persistTokenSession(
        authFixture,
        validClaims(exp=int((FIXED_NOW + timedelta(hours=1)).timestamp())),
    )
    audienceListToken = persistTokenSession(
        authFixture,
        validClaims(aud=["nubrix-admin-api"]),
    )

    assertUnauthorized(lambda: authFixture.service.verifyToken(oneHourToken))
    assertUnauthorized(lambda: authFixture.service.verifyToken(audienceListToken))


def test_verify_rejects_genuinely_expired_jwt_with_matching_database_session(authFixture):
    actualNow = datetime.now(timezone.utc)
    token = persistTokenSession(authFixture, validClaims(
        iat=int((actualNow - timedelta(hours=9)).timestamp()),
        exp=int((actualNow - timedelta(hours=1)).timestamp()),
    ))
    assert any(
        row["id"] == jwt.get_unverified_claims(token)["jti"]
        for row in authFixture.client.rows["admin_sessions"]
    )

    assertUnauthorized(lambda: authFixture.service.verifyToken(token))


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
        TEST_SECRET_KEY,
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
        TEST_SECRET_KEY,
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
        TEST_SECRET_KEY,
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
    assert sessionUpdates[0][2] == (
        ("id", authFixture.client.rows["admin_sessions"][0]["id"]),
        ("last_used_at", FIXED_NOW.isoformat()),
    )


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

    admittedKeys = [call[1] for call in authFixture.redis.calls if call[0] == "eval"]
    assert len(admittedKeys) == 2
    assert all(email not in key and ip not in key for key in admittedKeys)

    authFixture.redis.raiseOnAccess = True
    with patch("api.services.adminAuthService.logger.warning") as warning:
        with pytest.raises(AdminApiError):
            authFixture.service.login(email, password, ip)
    loggedArguments = repr(warning.call_args_list)
    assert email not in loggedArguments
    assert ip not in loggedArguments
    assert password not in loggedArguments
    assert "must-not-be-logged" not in loggedArguments


def test_throttle_atomically_admits_only_five_concurrent_email_attempts(authFixture):
    emailKey = "admin:login:email:" + hashlib.sha256(
        b"admin@example.com"
    ).hexdigest()
    authFixture.redis.readBarrierKey = emailKey
    authFixture.redis.readBarrier = threading.Barrier(6)

    def attempt(offset):
        try:
            authFixture.service.login(
                "admin@example.com", "wrong-password", f"203.0.113.{offset}"
            )
        except AdminApiError as exc:
            return exc.statusCode

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(attempt, range(1, 7)))

    assert statuses.count(401) == 5
    assert statuses.count(429) == 1
    assert authFixture.redis.counters[emailKey] == 5


def test_throttle_atomic_admission_repairs_a_missing_ttl(authFixture):
    emailKey = "admin:login:email:" + hashlib.sha256(
        b"admin@example.com"
    ).hexdigest()
    authFixture.redis.counters[emailKey] = 1
    assert emailKey not in authFixture.redis.ttls

    with pytest.raises(AdminApiError) as failure:
        authFixture.service.login(
            "admin@example.com", "wrong-password", "203.0.113.10"
        )

    assert failure.value.statusCode == 401
    assert authFixture.redis.ttls[emailKey] == 15 * 60


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


def test_missing_secret_500_releases_both_throttle_reservations(
    authFixture, monkeypatch
):
    monkeypatch.delenv("SECRET_KEY")

    with pytest.raises(AdminApiError) as captured:
        authFixture.service.login(
            "admin@example.com", VALID_PASSWORD, "203.0.113.10"
        )

    assert captured.value.statusCode == 500
    assert captured.value.message == "Admin authentication is unavailable"
    assertThrottleReservationsReleased(authFixture)


def test_lookup_500_releases_both_throttle_reservations(authFixture):
    authFixture.client.failureOperation = ("admin_users", "select")

    with pytest.raises(AdminApiError) as captured:
        authFixture.service.login(
            "admin@example.com", VALID_PASSWORD, "203.0.113.10"
        )

    assert captured.value.statusCode == 500
    assert captured.value.message == "Admin authentication is unavailable"
    assertThrottleReservationsReleased(authFixture)


def test_persistence_500_releases_both_throttle_reservations(authFixture):
    authFixture.client.failureOperation = ("admin_sessions", "insert")

    with pytest.raises(AdminApiError) as captured:
        authFixture.service.login(
            "admin@example.com", VALID_PASSWORD, "203.0.113.10"
        )

    assert captured.value.statusCode == 500
    assert captured.value.message == "Admin authentication is unavailable"
    assertThrottleReservationsReleased(authFixture)


def test_throttle_rollback_failure_does_not_mask_original_error_or_log_secrets(
    authFixture
):
    authFixture.client.failureOperation = ("admin_users", "select")
    authFixture.redis.raiseOnRelease = True

    with patch("api.services.adminAuthService.logger.warning") as warning:
        with pytest.raises(AdminApiError) as captured:
            authFixture.service.login(
                "admin@example.com", VALID_PASSWORD, "203.0.113.10"
            )

    assert captured.value.statusCode == 500
    assert captured.value.message == "Admin authentication is unavailable"
    assert warning.call_count == 2
    loggedArguments = repr(warning.call_args_list)
    assert "admin@example.com" not in loggedArguments
    assert "203.0.113.10" not in loggedArguments
    assert VALID_PASSWORD not in loggedArguments
    assert "must-not-be-logged" not in loggedArguments


def test_token_signed_with_secret_key_verifies(authFixture):
    response = authFixture.service.login(
        "admin@example.com", VALID_PASSWORD, "203.0.113.10"
    )
    context = authFixture.service.verifyToken(response["token"])

    assert context.adminId == ADMIN_ID


def test_missing_secret_key_is_unavailable(authFixture, monkeypatch):
    monkeypatch.delenv("SECRET_KEY")

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


def test_product_token_cannot_be_replayed_on_admin_routes_under_shared_secret(
    authFixture,
):
    """
    A product token signed with SECRET_KEY must not authenticate an admin.

    Admin tokens use the product SECRET_KEY, but admin verification still
    requires admin-specific claims and a matching persisted session.
    """
    productToken = jwt.encode(
        {
            "userId": ADMIN_ID,
            "sub": ADMIN_ID,
            "exp": int((FIXED_NOW + timedelta(hours=8)).timestamp()),
            "iat": int(FIXED_NOW.timestamp()),
        },
        TEST_SECRET_KEY,
        algorithm="HS256",
    )

    assertUnauthorized(lambda: authFixture.service.verifyToken(productToken))


@pytest.fixture(autouse=True)
def stubLifecycleAudit():
    """Keep CLI lifecycle audit writes off the network in unit tests."""
    recorder = SimpleNamespace(calls=[])
    recorder.record = lambda **kwargs: recorder.calls.append(kwargs)
    with patch(
        "api.services.adminAuditService.getAdminAuditService",
        return_value=recorder,
    ):
        yield recorder


def liveSession(sessionId, adminId, revokedAt=None):
    return {
        "id": sessionId,
        "admin_id": adminId,
        "token_hash": "a" * 64,
        "created_at": FIXED_NOW.isoformat(),
        "expires_at": (FIXED_NOW + timedelta(hours=8)).isoformat(),
        "revoked_at": revokedAt,
        "last_used_at": FIXED_NOW.isoformat(),
    }


def test_list_admins_returns_safe_fields_only(authFixture):
    admins = authFixture.service.listAdmins()

    assert len(admins) == 2
    assert set(admins[0]) == {
        "id", "email", "name", "is_active", "last_login_at", "created_at",
    }
    assert all("password_hash" not in admin for admin in admins)


def test_list_admins_never_selects_the_password_hash(authFixture):
    authFixture.service.listAdmins()

    selected = [
        call for call in authFixture.client.selectFields
        if call[0] == "admin_users"
    ]
    assert selected, "expected a select against admin_users"
    assert all("password_hash" not in call[1] for call in selected)


def test_deactivate_sets_flag_and_revokes_live_sessions(authFixture):
    authFixture.client.rows["admin_sessions"].append(
        liveSession("session-1", ADMIN_ID)
    )
    authFixture.client.rows["admin_sessions"].append(
        liveSession("session-2", OTHER_ADMIN_ID)
    )

    result = authFixture.service.setAdminActive("admin@example.com", False)

    assert result["is_active"] is False
    assert result["revokedSessions"] == 1
    assert authFixture.admins[0]["is_active"] is False
    revoked = {
        row["id"]: row["revoked_at"]
        for row in authFixture.client.rows["admin_sessions"]
    }
    assert revoked["session-1"] == FIXED_NOW.isoformat()
    assert revoked["session-2"] is None


def test_deactivate_is_case_insensitive_on_email(authFixture):
    result = authFixture.service.setAdminActive("ADMIN@Example.COM", False)

    assert result["id"] == ADMIN_ID
    assert authFixture.admins[0]["is_active"] is False


def test_activate_restores_flag_without_revoking_sessions(authFixture):
    authFixture.admins[0]["is_active"] = False
    authFixture.client.rows["admin_sessions"].append(
        liveSession("session-1", ADMIN_ID)
    )

    result = authFixture.service.setAdminActive("admin@example.com", True)

    assert result["is_active"] is True
    assert result["revokedSessions"] == 0
    assert authFixture.client.rows["admin_sessions"][0]["revoked_at"] is None


def test_change_password_replaces_hash_and_revokes_sessions(authFixture):
    authFixture.client.rows["admin_sessions"].append(
        liveSession("session-1", ADMIN_ID)
    )
    oldHash = authFixture.admins[0]["password_hash"]

    result = authFixture.service.changeAdminPassword(
        "admin@example.com", "a brand new password"
    )

    assert result["revokedSessions"] == 1
    assert authFixture.admins[0]["password_hash"] != oldHash
    assert authFixture.client.rows["admin_sessions"][0]["revoked_at"] is not None


def test_password_after_reset_authenticates_and_old_one_does_not(authFixture):
    authFixture.service.changeAdminPassword(
        "admin@example.com", "a brand new password"
    )

    assertUnauthorized(
        lambda: authFixture.service.login(
            "admin@example.com", VALID_PASSWORD, "10.0.0.1"
        )
    )
    response = authFixture.service.login(
        "admin@example.com", "a brand new password", "10.0.0.2"
    )
    assert response["admin"]["id"] == ADMIN_ID


def test_change_password_enforces_minimum_length(authFixture):
    with pytest.raises(AdminApiError) as captured:
        authFixture.service.changeAdminPassword("admin@example.com", "short")

    assert captured.value.statusCode == 422
    assert "password" in captured.value.errors


def test_change_password_enforces_maximum_length(authFixture):
    with pytest.raises(AdminApiError) as captured:
        authFixture.service.changeAdminPassword("admin@example.com", "x" * 129)

    assert captured.value.statusCode == 422
    assert "password" in captured.value.errors


def test_change_password_rejects_before_touching_storage(authFixture):
    before = authFixture.admins[0]["password_hash"]

    with pytest.raises(AdminApiError):
        authFixture.service.changeAdminPassword("admin@example.com", "short")

    assert authFixture.admins[0]["password_hash"] == before


def test_lifecycle_on_unknown_email_is_not_found(authFixture):
    with pytest.raises(AdminApiError) as captured:
        authFixture.service.setAdminActive("nobody@example.com", False)

    assert captured.value.statusCode == 404
    assert captured.value.message == "Administrator not found"


def test_lifecycle_on_malformed_email_is_validation_error(authFixture):
    with pytest.raises(AdminApiError) as captured:
        authFixture.service.changeAdminPassword("not-an-email", "a valid password")

    assert captured.value.statusCode == 422
    assert "email" in captured.value.errors


def test_revoke_all_sessions_skips_already_revoked(authFixture):
    authFixture.client.rows["admin_sessions"].append(
        liveSession("session-1", ADMIN_ID, revokedAt="2026-01-01T00:00:00+00:00")
    )
    authFixture.client.rows["admin_sessions"].append(
        liveSession("session-2", ADMIN_ID)
    )

    revoked = authFixture.service.revokeAllSessions(ADMIN_ID)

    assert revoked == 1
    assert authFixture.client.rows["admin_sessions"][0]["revoked_at"] == (
        "2026-01-01T00:00:00+00:00"
    )


def test_lifecycle_actions_are_audited(authFixture, stubLifecycleAudit):
    authFixture.service.setAdminActive("admin@example.com", False)
    authFixture.service.changeAdminPassword("admin@example.com", "a valid password")

    actions = [call["action"] for call in stubLifecycleAudit.calls]
    assert actions == ["admin.deactivate", "admin.password_reset"]
    assert all(call["targetType"] == "admin" for call in stubLifecycleAudit.calls)
    assert all(call["outcome"] == "success" for call in stubLifecycleAudit.calls)


def test_lifecycle_succeeds_when_audit_backend_is_down(authFixture):
    with patch(
        "api.services.adminAuditService.getAdminAuditService",
        side_effect=RuntimeError("audit down"),
    ):
        result = authFixture.service.setAdminActive("admin@example.com", False)

    assert result["is_active"] is False
    assert authFixture.admins[0]["is_active"] is False
