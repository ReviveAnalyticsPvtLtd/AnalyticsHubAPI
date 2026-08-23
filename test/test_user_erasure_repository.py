import pytest

from api.adminErrors import AdminApiError


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split()).lower()
        self.state["executed"].append((normalized, params))
        if 'from public."users"' in normalized:
            self.current = {"exists": 1} if self.state["userExists"] else None
        elif "where idempotency_key = %s" in normalized:
            self.current = self.state.get("idempotencyRow")
        elif "status <> 'completed'" in normalized and "target_user_id" in normalized:
            self.current = self.state.get("activeRow")
        elif "insert into public.user_erasure_requests" in normalized:
            if self.state.get("insertError"):
                raise self.state["insertError"]
            self.current = {
                "id": "request-1",
                "target_user_id": params[0],
                "subject_fingerprint": params[1],
                "idempotency_key": params[3],
                "status": "PENDING",
                "created_at": "2026-08-24T10:00:00+00:00",
            }
        elif "from public.user_erasure_requests" in normalized:
            self.current = self.state.get("requestRow")
        else:
            self.current = None

    def executemany(self, query, params):
        normalized = " ".join(str(query).split()).lower()
        values = list(params)
        self.state["executemany"].append((normalized, values))

    def fetchone(self):
        return self.current

    def fetchall(self):
        return list(self.state.get("stepRows", []))


class FakeConnection:
    def __init__(self, **overrides):
        self.state = {
            "executed": [],
            "executemany": [],
            "userExists": True,
            **overrides,
        }
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, **_kwargs):
        return FakeCursor(self.state)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def _repository(connection):
    from api.services.userErasureRepository import UserErasureRepository

    return UserErasureRepository(connectionFactory=lambda: connection)


def test_create_request_persists_all_steps_and_freezes_billing_transactionally():
    connection = FakeConnection()
    repository = _repository(connection)

    row = repository.createRequest(
        userId="user-1",
        subjectFingerprint="a" * 64,
        adminId="admin-1",
        idempotencyKey="8cfdb150-417d-47ab-acd1-fef39d2bc14e",
        reason=None,
    )

    assert row["id"] == "request-1"
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed == 1
    assert len(connection.state["executemany"][0][1]) == 8
    assert [params[1] for params in connection.state["executemany"][0][1]] == [
        "revoke_access",
        "inventory",
        "stop_billing",
        "delete_storage",
        "delete_transient_state",
        "delete_auth_identity",
        "delete_database_data",
        "verify_and_finalize",
    ]
    assert any(
        "set erasure_pending = true" in query
        and "auto_renew_enabled = false" in query
        for query, _params in connection.state["executed"]
    )


def test_create_request_rejects_missing_user_and_active_workflow():
    missing = FakeConnection(userExists=False)
    with pytest.raises(AdminApiError) as captured:
        _repository(missing).createRequest(
            "missing", "a" * 64, "admin-1",
            "8cfdb150-417d-47ab-acd1-fef39d2bc14e", None,
        )
    assert captured.value.statusCode == 404
    assert missing.rollbacks == 1
    assert missing.commits == 0

    active = FakeConnection(activeRow={"id": "existing", "status": "IN_PROGRESS"})
    with pytest.raises(AdminApiError) as captured:
        _repository(active).createRequest(
            "user-1", "a" * 64, "admin-1",
            "8cfdb150-417d-47ab-acd1-fef39d2bc14e", None,
        )
    assert captured.value.statusCode == 409
    assert captured.value.message == "User already has an active erasure request"
    assert active.rollbacks == 1


def test_find_by_idempotency_and_get_request_include_ordered_steps():
    requestRow = {
        "id": "request-1",
        "subject_fingerprint": "a" * 64,
        "status": "PENDING",
        "created_at": "2026-08-24T10:00:00+00:00",
    }
    steps = [
        {"step_name": "revoke_access", "status": "COMPLETED"},
        {"step_name": "inventory", "status": "PENDING"},
    ]
    connection = FakeConnection(
        idempotencyRow=requestRow,
        requestRow=requestRow,
        stepRows=steps,
    )
    repository = _repository(connection)

    found = repository.findByIdempotency(
        "8cfdb150-417d-47ab-acd1-fef39d2bc14e"
    )
    loaded = repository.getRequest("request-1")

    assert found == requestRow
    assert loaded == {**requestRow, "steps": steps}
    assert connection.commits == 0
    assert connection.rollbacks == 0
    assert connection.closed == 2


def test_create_request_rolls_back_and_closes_on_database_error():
    connection = FakeConnection(insertError=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        _repository(connection).createRequest(
            "user-1", "a" * 64, "admin-1",
            "8cfdb150-417d-47ab-acd1-fef39d2bc14e", None,
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed == 1
