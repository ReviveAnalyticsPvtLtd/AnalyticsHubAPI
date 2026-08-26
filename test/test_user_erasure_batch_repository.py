import hashlib
import re
import uuid

import pytest

from api.adminErrors import AdminApiError
from api.services.userErasureRepository import ERASURE_STEP_NAMES


KEY = "8cfdb150-417d-47ab-acd1-fef39d2bc14e"
BATCH_ID = "414c6f2b-a630-49c2-89eb-444514479384"


def fingerprint(userId: str) -> str:
    return hashlib.sha256(f"test-hmac:{userId}".encode()).hexdigest()


def _item(ordinal: int, userId: str) -> dict:
    return {
        "ordinal": ordinal,
        "userId": userId,
        "subjectFingerprint": fingerprint(userId),
    }


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.current = None
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split()).lower()
        self.state["executed"].append((normalized, params))
        self.current = None
        self.rows = []
        if 'from public."users"' in normalized:
            requested = set(params[0])
            self.rows = [
                {"userId": userId}
                for userId in self.state["users"]
                if userId in requested
            ]
        elif (
            "from public.user_erasure_requests" in normalized
            and "target_user_id = any(%s)" in normalized
        ):
            self.rows = list(self.state["history"])
        elif "insert into public.user_erasure_batches" in normalized:
            if self.state.get("batchInsertError"):
                raise self.state["batchInsertError"]
            self.current = {
                "id": "batch-1",
                "requested_by": params[0],
                "idempotency_key": params[1],
                "request_hash": params[2],
                "status": "PREVIEWED",
                "reason": params[3],
                "requested_count": params[4],
                "ready_count": params[5],
                "expires_at": "2026-08-26T10:15:00+00:00",
                "created_at": "2026-08-26T10:00:00+00:00",
                "updated_at": "2026-08-26T10:00:00+00:00",
                "confirmed_at": None,
                "completed_at": None,
            }
        elif "from public.user_erasure_batches" in normalized:
            self.current = self.state.get("batchRow")
        elif "from public.user_erasure_batch_items" in normalized:
            self.rows = list(self.state.get("itemRows", []))

    def executemany(self, query, params):
        normalized = " ".join(str(query).split()).lower()
        values = list(params)
        self.state["executemany"].append((normalized, values))
        if self.state.get("itemInsertError"):
            raise self.state["itemInsertError"]

    def fetchone(self):
        return self.current

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, *, users=(), history=(), **overrides):
        self.state = {
            "executed": [],
            "executemany": [],
            "users": list(users),
            "history": list(history),
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
    from api.services.userErasureBatchRepository import UserErasureBatchRepository

    return UserErasureBatchRepository(connectionFactory=lambda: connection)


def connection_with(*, users=(), active=None, completed=None, **overrides):
    history = []
    for userId, requestId in (active or {}).items():
        history.append({
            "id": requestId,
            "target_user_id": userId,
            "subject_fingerprint": fingerprint(userId),
            "status": "IN_PROGRESS",
        })
    for subjectFingerprint, requestId in (completed or {}).items():
        history.append({
            "id": requestId,
            "target_user_id": None,
            "subject_fingerprint": subjectFingerprint,
            "status": "COMPLETED",
        })
    return FakeConnection(users=users, history=history, **overrides)


def test_create_preview_classifies_all_subjects_in_input_order():
    connection = connection_with(
        users={"ready-user", "active-user", "done-user"},
        active={"active-user": "request-active"},
        completed={fingerprint("done-user"): "request-done"},
    )
    repository = _repository(connection)

    batch = repository.createPreview(
        userItems=[
            _item(0, "ready-user"),
            _item(1, "active-user"),
            _item(2, "done-user"),
            _item(3, "missing-user"),
        ],
        adminId="admin-1",
        idempotencyKey=KEY,
        requestHash="a" * 64,
        reason=None,
    )

    assert [item["classification"] for item in batch["items"]] == [
        "READY",
        "ALREADY_IN_PROGRESS",
        "ALREADY_COMPLETED",
        "USER_NOT_FOUND",
    ]
    assert [item["request_id"] for item in batch["items"]] == [
        None,
        "request-active",
        "request-done",
        None,
    ]
    assert batch["ready_count"] == 1
    assert batch["items"][3]["error_code"] == "USER_NOT_FOUND"


def test_create_preview_uses_two_bounded_reads_and_one_transaction():
    connection = connection_with(users={"user-1", "user-2"})
    repository = _repository(connection)
    userItems = [_item(0, "user-2"), _item(1, "user-1")]

    repository.createPreview(
        userItems=userItems,
        adminId="admin-1",
        idempotencyKey=KEY,
        requestHash="b" * 64,
        reason="reviewed",
    )

    userQueries = [
        call for call in connection.state["executed"]
        if 'from public."users"' in call[0]
    ]
    historyQueries = [
        call for call in connection.state["executed"]
        if "from public.user_erasure_requests" in call[0]
    ]
    batchInserts = [
        call for call in connection.state["executed"]
        if "insert into public.user_erasure_batches" in call[0]
    ]
    assert len(userQueries) == 1
    assert userQueries[0][1] == (["user-2", "user-1"],)
    assert "= any(%s)" in userQueries[0][0]
    assert len(historyQueries) == 1
    assert historyQueries[0][1] == (
        ["user-2", "user-1"],
        [fingerprint("user-2"), fingerprint("user-1")],
    )
    assert "target_user_id = any(%s)" in historyQueries[0][0]
    assert "subject_fingerprint = any(%s)" in historyQueries[0][0]
    assert len(batchInserts) == 1
    assert len(connection.state["executemany"]) == 1
    assert len(connection.state["executemany"][0][1]) == 2
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed == 1

    allWrites = [
        query for query, _params in connection.state["executed"]
        if query.startswith(("insert ", "update ", "delete "))
    ]
    allWrites.extend(
        query for query, _params in connection.state["executemany"]
        if query.startswith(("insert ", "update ", "delete "))
    )
    assert len(allWrites) == 2
    writtenTables = []
    for query in allWrites:
        match = re.match(r"(?:insert into|update|delete from)\s+([^\s(]+)", query)
        assert match is not None
        writtenTables.append(match.group(1))
    assert writtenTables == [
        "public.user_erasure_batches",
        "public.user_erasure_batch_items",
    ]


def test_create_preview_prefers_active_history_over_completed_history():
    subject = _item(0, "overlap-user")
    connection = connection_with(
        users={"overlap-user"},
        active={"overlap-user": "request-active"},
        completed={subject["subjectFingerprint"]: "request-completed"},
    )

    batch = _repository(connection).createPreview(
        userItems=[subject],
        adminId="admin-1",
        idempotencyKey=KEY,
        requestHash="c" * 64,
        reason=None,
    )

    assert batch["items"][0]["classification"] == "ALREADY_IN_PROGRESS"
    assert batch["items"][0]["request_id"] == "request-active"


def test_create_preview_rolls_back_and_closes_on_persistence_failure():
    connection = connection_with(
        users={"user-1"},
        itemInsertError=RuntimeError("item insert failed"),
    )

    with pytest.raises(RuntimeError, match="item insert failed"):
        _repository(connection).createPreview(
            userItems=[_item(0, "user-1")],
            adminId="admin-1",
            idempotencyKey=KEY,
            requestHash="d" * 64,
            reason=None,
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed == 1


def test_find_by_idempotency_and_get_batch_load_ordered_safe_child_state():
    batchRow = {
        "id": "batch-1",
        "status": "IN_PROGRESS",
        "requested_count": 2,
        "ready_count": 1,
    }
    itemRows = [
        {
            "id": "item-1",
            "ordinal": 0,
            "target_user_id": "user-1",
            "classification": "READY",
            "request_id": "request-1",
            "request_status": "IN_PROGRESS",
            "request_created_at": "2026-08-26T10:01:00+00:00",
            "request_updated_at": "2026-08-26T10:02:00+00:00",
            "request_started_at": "2026-08-26T10:02:00+00:00",
            "request_completed_at": None,
        },
        {
            "id": "item-2",
            "ordinal": 1,
            "target_user_id": None,
            "classification": "ALREADY_COMPLETED",
            "request_id": "request-2",
            "request_status": "COMPLETED",
            "request_created_at": "2026-08-25T10:00:00+00:00",
            "request_updated_at": "2026-08-25T10:05:00+00:00",
            "request_started_at": "2026-08-25T10:01:00+00:00",
            "request_completed_at": "2026-08-25T10:05:00+00:00",
        },
    ]
    connection = FakeConnection(batchRow=batchRow, itemRows=itemRows)
    repository = _repository(connection)

    found = repository.findByIdempotency(KEY)
    loaded = repository.getBatch("batch-1")

    assert found == {**batchRow, "items": itemRows}
    assert loaded == {**batchRow, "items": itemRows}
    assert connection.commits == 0
    assert connection.rollbacks == 0
    assert connection.closed == 2
    itemQueries = [
        query for query, _params in connection.state["executed"]
        if "from public.user_erasure_batch_items" in query
    ]
    assert len(itemQueries) == 2
    assert all("order by item.ordinal" in query for query in itemQueries)
    assert all("left join public.user_erasure_requests" in query for query in itemQueries)
    assert all("last_error" not in query and "details" not in query for query in itemQueries)


def test_default_connection_uses_dedicated_application_name(monkeypatch):
    import api.services.userErasureBatchRepository as module

    connection = FakeConnection(batchRow=None)
    captured = {}

    def connect(databaseUrl, **kwargs):
        captured.update({"databaseUrl": databaseUrl, **kwargs})
        return connection

    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setattr(module.psycopg2, "connect", connect)

    assert module.UserErasureBatchRepository().getBatch("missing") is None
    assert captured == {
        "databaseUrl": "postgresql://example.invalid/db",
        "application_name": "nubrix-user-erasure-batch",
    }
    assert connection.closed == 1


def test_repository_factory_returns_a_single_repository(monkeypatch):
    import api.services.userErasureBatchRepository as module

    monkeypatch.setattr(module, "_userErasureBatchRepository", None)
    first = module.getUserErasureBatchRepository()
    second = module.getUserErasureBatchRepository()

    assert isinstance(first, module.UserErasureBatchRepository)
    assert second is first


class ConfirmCursor:
    def __init__(self, state):
        self.state = state
        self.current = None
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split()).lower()
        self.state["executed"].append((normalized, params))
        self.current = None
        self.rows = []
        self.rowcount = 0
        failOn = self.state.get("failOn")
        if failOn and failOn in normalized:
            raise RuntimeError("postgresql://secret-password")
        if (
            "from public.user_erasure_batches as batch" in normalized
            and "for update" in normalized
        ):
            self.current = self.state.get("batch")
        elif "from public.admin_sessions" in normalized:
            self.current = self.state.get("session")
        elif (
            "from public.user_erasure_batch_items as item" in normalized
            and "for update" in normalized
        ):
            self.rows = [dict(item) for item in self.state["items"]]
        elif "pg_advisory_xact_lock" in normalized:
            self.current = {"locked": True}
        elif 'from public."users"' in normalized:
            requested = set(params[0])
            self.rows = [
                {"userId": userId}
                for userId in sorted(self.state["users"])
                if userId in requested
            ]
        elif (
            "from public.user_erasure_requests" in normalized
            and "target_user_id = any(%s)" in normalized
        ):
            self.rows = [dict(row) for row in self.state["history"]]
        elif 'update public."users"' in normalized:
            self.rowcount = int(params[3] in self.state["users"])
        elif "update public.user_erasure_batch_items" in normalized:
            classification, requestId, errorCode, itemId = params
            item = next(item for item in self.state["items"] if item["id"] == itemId)
            item.update({
                "classification": classification,
                "request_id": requestId,
                "error_code": errorCode,
            })
            self.rowcount = 1
        elif "insert into public.user_erasure_requests" in normalized:
            userId, subjectFingerprint, adminId, idempotencyKey, reason = params
            request = {
                "id": self.state["requestIds"].get(
                    userId, f"request-{len(self.state['requests']) + 1}"
                ),
                "target_user_id": userId,
                "subject_fingerprint": subjectFingerprint,
                "requested_by": adminId,
                "idempotency_key": idempotencyKey,
                "status": "PENDING",
                "reason": reason,
                "created_at": "2026-08-26T10:01:00+00:00",
            }
            self.state["requests"].append(request)
            self.current = request
            self.rowcount = 1
        elif "update public.user_erasure_batches" in normalized:
            self.state["batch"].update({
                "status": "IN_PROGRESS",
                "confirmed_at": "2026-08-26T10:01:00+00:00",
            })
            self.current = dict(self.state["batch"])
            self.rowcount = 1
        elif "from public.user_erasure_batch_items as item" in normalized:
            requestStatuses = {
                request["id"]: request["status"]
                for request in [*self.state["history"], *self.state["requests"]]
            }
            self.rows = [
                {
                    **item,
                    "request_status": requestStatuses.get(item.get("request_id")),
                }
                for item in sorted(self.state["items"], key=lambda row: row["ordinal"])
            ]

    def executemany(self, query, params):
        normalized = " ".join(str(query).split()).lower()
        values = list(params)
        self.state["executemany"].append((normalized, values))
        failOnMany = self.state.get("failOnMany")
        if failOnMany and failOnMany in normalized:
            raise RuntimeError("postgresql://secret-password")
        self.rowcount = len(values)

    def fetchone(self):
        return self.current

    def fetchall(self):
        return list(self.rows)


class ConfirmConnection:
    def __init__(
        self,
        *,
        requestedBy="admin-1",
        status="PREVIEWED",
        readyCount=1,
        expired=False,
        session=None,
        users=("user-a",),
        items=None,
        history=(),
        requestIds=None,
        failOn=None,
        failOnMany=None,
    ):
        items = items or [{
            "id": "item-a",
            "batch_id": BATCH_ID,
            "ordinal": 0,
            "target_user_id": "user-a",
            "subject_fingerprint": fingerprint("user-a"),
            "classification": "READY",
            "request_id": None,
            "error_code": None,
        }]
        self.state = {
            "executed": [],
            "executemany": [],
            "batch": {
                "id": BATCH_ID,
                "requested_by": requestedBy,
                "status": status,
                "reason": "support-request",
                "requested_count": len(items),
                "ready_count": readyCount,
                "expires_at": "2026-08-26T10:15:00+00:00",
                "is_expired": expired,
                "confirmed_at": None,
            },
            "session": (
                {"created_at": "2026-08-26T10:00:00+00:00"}
                if session is None
                else session or None
            ),
            "users": set(users),
            "items": [dict(item) for item in items],
            "history": [dict(row) for row in history],
            "requests": [],
            "requestIds": requestIds or {},
            "failOn": failOn,
            "failOnMany": failOnMany,
        }
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, **_kwargs):
        return ConfirmCursor(self.state)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def _ready_item(ordinal: int, userId: str, itemId: str) -> dict:
    return {
        "id": itemId,
        "batch_id": BATCH_ID,
        "ordinal": ordinal,
        "target_user_id": userId,
        "subject_fingerprint": fingerprint(userId),
        "classification": "READY",
        "request_id": None,
        "error_code": None,
    }


def _confirm(repository, confirmation="ERASE 1 USER"):
    return repository.confirmBatch(
        batchId=BATCH_ID,
        adminId="admin-1",
        sessionId="session-recent",
        confirmation=confirmation,
    )


def test_confirm_locks_ready_users_in_sorted_order_and_creates_children():
    items = [
        _ready_item(0, "user-b", "item-b"),
        _ready_item(1, "user-a", "item-a"),
    ]
    connection = ConfirmConnection(
        users={"user-a", "user-b"},
        items=items,
        readyCount=2,
        requestIds={"user-a": "request-a", "user-b": "request-b"},
    )

    batch = _confirm(_repository(connection), "ERASE 2 USERS")

    executed = connection.state["executed"]
    lockParams = [
        params[0] for sql, params in executed if "pg_advisory_xact_lock" in sql
    ]
    assert lockParams == ["user-a", "user-b"]
    assert "for update" in next(
        sql for sql, _ in executed if "from public.user_erasure_batches as batch" in sql
    )
    assert "for update" in next(
        sql for sql, _ in executed if "from public.user_erasure_batch_items as item" in sql
    )
    sessionSql, sessionParams = next(
        call for call in executed if "from public.admin_sessions" in call[0]
    )
    assert sessionParams == ("session-recent", "admin-1")
    assert "revoked_at is null" in sessionSql
    assert "expires_at > now()" in sessionSql
    assert "created_at >= now() - interval '10 minutes'" in sessionSql
    assert "for share" in sessionSql

    childInserts = [
        params for sql, params in executed
        if "insert into public.user_erasure_requests" in sql
    ]
    assert [params[0] for params in childInserts] == ["user-b", "user-a"]
    assert {params[3] for params in childInserts} == {
        str(uuid.uuid5(uuid.UUID(BATCH_ID), "item-a")),
        str(uuid.uuid5(uuid.UUID(BATCH_ID), "item-b")),
    }
    assert all(params[4] == "support-request" for params in childInserts)
    stepInserts = [
        values for sql, values in connection.state["executemany"]
        if "insert into public.user_erasure_steps" in sql
    ]
    assert len(stepInserts) == 2
    assert all([value[1] for value in values] == list(ERASURE_STEP_NAMES) for values in stepInserts)

    assert sum('update public."users"' in sql for sql, _ in executed) == 2
    assert sum('delete from public."sessions"' in sql for sql, _ in executed) == 2
    assert sum("update public.subscriptions" in sql for sql, _ in executed) == 2
    assert sum("update public.admin_free_trial_extension_items" in sql for sql, _ in executed) == 2
    assert batch["status"] == "IN_PROGRESS"
    assert [item["request_id"] for item in batch["items"]] == ["request-b", "request-a"]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed == 1


def test_confirm_uses_transition_aware_authoritative_ban_metadata():
    connection = ConfirmConnection()

    _confirm(_repository(connection))

    banSql, params = next(
        call for call in connection.state["executed"]
        if 'update public."users"' in call[0]
    )
    assert 'case when not "isbanned"' in banSql
    assert 'else coalesce(%s, "banreason")' in banSql
    assert params == (
        "admin-1", "support-request", "support-request", "user-a"
    )


def test_confirm_rejects_another_creator_before_session_or_mutation():
    connection = ConfirmConnection(requestedBy="admin-other")

    with pytest.raises(AdminApiError) as captured:
        _confirm(_repository(connection))

    assert (captured.value.statusCode, captured.value.message) == (
        403,
        "Only the administrator who created the preview can confirm it",
    )
    assert not any("from public.admin_sessions" in sql for sql, _ in connection.state["executed"])
    assert connection.commits == 0
    assert connection.rollbacks == 1


@pytest.mark.parametrize("sessionScenario", ["older", "revoked", "expired"])
def test_confirm_rejects_session_that_is_not_active_and_recent(sessionScenario):
    connection = ConfirmConnection(session={})
    connection.state["sessionScenario"] = sessionScenario

    with pytest.raises(AdminApiError) as captured:
        _confirm(_repository(connection))

    assert (captured.value.statusCode, captured.value.message) == (
        403,
        "A recent administrator login is required to confirm user erasure",
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_confirm_rejects_expired_preview():
    connection = ConfirmConnection(expired=True)

    with pytest.raises(AdminApiError) as captured:
        _confirm(_repository(connection))

    assert (captured.value.statusCode, captured.value.message) == (
        409,
        "User erasure batch preview has expired",
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_confirm_expired_preview_wins_over_wrong_phrase_and_stale_session():
    connection = ConfirmConnection(
        expired=True,
        readyCount=2,
        session={},
    )

    with pytest.raises(AdminApiError) as captured:
        _confirm(_repository(connection), "ERASE 1 USER")

    assert (captured.value.statusCode, captured.value.message) == (
        409,
        "User erasure batch preview has expired",
    )
    assert not any(
        "from public.admin_sessions" in query
        for query, _params in connection.state["executed"]
    )
    assert connection.rollbacks == 1


def test_confirm_rejects_wrong_count_bound_phrase():
    connection = ConfirmConnection(readyCount=2)

    with pytest.raises(AdminApiError) as captured:
        _confirm(_repository(connection), "ERASE 1 USER")

    assert (captured.value.statusCode, captured.value.message) == (
        422,
        "Confirmation does not match the reviewed user count",
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_confirm_rejects_preview_with_no_ready_subjects():
    connection = ConfirmConnection(
        readyCount=0,
        users=(),
        items=[{
            **_ready_item(0, "missing", "item-missing"),
            "classification": "USER_NOT_FOUND",
            "error_code": "USER_NOT_FOUND",
        }],
    )

    with pytest.raises(AdminApiError) as captured:
        _confirm(_repository(connection))

    assert (captured.value.statusCode, captured.value.message) == (
        409,
        "User erasure batch has no ready users",
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_confirm_replay_returns_same_children_without_duplicate_mutations():
    item = {
        **_ready_item(0, "user-a", "item-a"),
        "request_id": "request-existing",
    }
    connection = ConfirmConnection(
        status="IN_PROGRESS",
        items=[item],
        history=[{
            "id": "request-existing",
            "target_user_id": "user-a",
            "subject_fingerprint": fingerprint("user-a"),
            "status": "PENDING",
        }],
    )
    repository = _repository(connection)

    first = _confirm(repository)
    second = _confirm(repository)

    assert first["items"][0]["request_id"] == "request-existing"
    assert second["items"][0]["request_id"] == "request-existing"
    writes = [
        sql for sql, _ in connection.state["executed"]
        if sql.startswith(("insert ", "update ", "delete "))
    ]
    assert writes == []
    assert connection.state["executemany"] == []
    assert connection.commits == 2
    assert connection.rollbacks == 0


def test_confirm_replay_is_independent_of_preview_phrase_ready_count_and_session():
    item = {
        **_ready_item(0, "user-a", "item-a"),
        "request_id": "request-existing",
    }
    connection = ConfirmConnection(
        status="IN_PROGRESS",
        readyCount=0,
        session={},
        items=[item],
        history=[{
            "id": "request-existing",
            "target_user_id": "user-a",
            "subject_fingerprint": fingerprint("user-a"),
            "status": "PENDING",
        }],
    )

    batch = _confirm(_repository(connection), "ERASE 25 USERS")

    assert batch["status"] == "IN_PROGRESS"
    assert batch["items"][0]["request_id"] == "request-existing"
    assert not any(
        "from public.admin_sessions" in query
        for query, _params in connection.state["executed"]
    )
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_confirm_reclassifies_user_that_disappeared_after_preview():
    connection = ConfirmConnection(users=())

    batch = _confirm(_repository(connection))

    assert batch["items"][0]["classification"] == "USER_NOT_FOUND"
    assert batch["items"][0]["request_id"] is None
    assert batch["items"][0]["error_code"] == "USER_NOT_FOUND"
    assert connection.state["requests"] == []
    assert connection.commits == 1


def test_confirm_links_new_active_request_race_before_completed_history():
    history = [
        {
            "id": "request-completed",
            "target_user_id": None,
            "subject_fingerprint": fingerprint("user-a"),
            "status": "COMPLETED",
        },
        {
            "id": "request-active",
            "target_user_id": "user-a",
            "subject_fingerprint": fingerprint("user-a"),
            "status": "IN_PROGRESS",
        },
    ]
    connection = ConfirmConnection(history=history)

    batch = _confirm(_repository(connection))

    assert batch["items"][0]["classification"] == "ALREADY_IN_PROGRESS"
    assert batch["items"][0]["request_id"] == "request-active"
    assert connection.state["requests"] == []


def test_confirm_links_completed_request_race_before_missing_user():
    connection = ConfirmConnection(
        users=(),
        history=[{
            "id": "request-completed",
            "target_user_id": None,
            "subject_fingerprint": fingerprint("user-a"),
            "status": "COMPLETED",
        }],
    )

    batch = _confirm(_repository(connection))

    assert batch["items"][0]["classification"] == "ALREADY_COMPLETED"
    assert batch["items"][0]["request_id"] == "request-completed"
    assert connection.state["requests"] == []


def test_confirm_rolls_back_whole_transaction_on_unexpected_step_insert_failure():
    connection = ConfirmConnection(failOnMany="insert into public.user_erasure_steps")

    with pytest.raises(RuntimeError, match="secret-password"):
        _confirm(_repository(connection))

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed == 1


class ReconcileCursor:
    def __init__(self, state):
        self.state = state
        self.current = None
        self.rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split()).lower()
        self.state["executed"].append((normalized, params))
        self.current = None
        self.rows = []
        self.rowcount = 0
        if self.state.get("failOn") and self.state["failOn"] in normalized:
            raise RuntimeError("provider password must not leak")
        if (
            "select batch.id" in normalized
            and "from public.user_erasure_batches as batch" in normalized
            and "for update" not in normalized
        ):
            self.rows = [{"id": batchId} for batchId in self.state["claimable"]]
        elif (
            "from public.user_erasure_batches as batch" in normalized
            and "for update" in normalized
        ):
            batch = self.state.get("batch")
            self.current = (
                {**batch, "is_expired": self.state["expired"]}
                if batch is not None
                else None
            )
        elif (
            "update public.user_erasure_batch_items as item" in normalized
            and "request.status = 'completed'" in normalized
        ):
            for item in self.state["items"]:
                if item.get("request_status") == "COMPLETED":
                    item["target_user_id"] = None
            self.rowcount = 1
        elif (
            "update public.user_erasure_batch_items" in normalized
            and "set target_user_id = null" in normalized
        ):
            for item in self.state["items"]:
                item["target_user_id"] = None
            self.rowcount = len(self.state["items"])
        elif "update public.user_erasure_batches" in normalized:
            nextStatus, terminal, secondTerminal, batchId = params
            assert terminal is secondTerminal
            batch = self.state["batch"]
            assert str(batch["id"]) == str(batchId)
            batch["status"] = nextStatus
            if terminal:
                batch["reason"] = None
                batch["completed_at"] = (
                    batch.get("completed_at")
                    or "2026-08-26T12:00:00+00:00"
                )
            self.current = dict(batch)
            self.rowcount = 1
        elif "from public.user_erasure_batch_items as item" in normalized:
            self.rows = [dict(item) for item in self.state["items"]]

    def fetchone(self):
        return self.current

    def fetchall(self):
        return list(self.rows)


class ReconcileConnection:
    def __init__(
        self,
        *,
        status="IN_PROGRESS",
        childStatuses=("PENDING",),
        expired=False,
        claimable=(),
        failOn=None,
    ):
        self.state = {
            "executed": [],
            "claimable": list(claimable),
            "expired": expired,
            "failOn": failOn,
            "batch": {
                "id": BATCH_ID,
                "requested_by": "admin-1",
                "status": status,
                "reason": "support request",
                "requested_count": len(childStatuses),
                "ready_count": len(childStatuses),
                "expires_at": "2026-08-26T10:15:00+00:00",
                "confirmed_at": (
                    None
                    if status == "PREVIEWED"
                    else "2026-08-26T10:01:00+00:00"
                ),
                "completed_at": None,
            },
            "items": [
                {
                    "id": f"item-{index}",
                    "batch_id": BATCH_ID,
                    "ordinal": index,
                    "target_user_id": f"user-{index}",
                    "classification": "READY",
                    "request_id": f"request-{index}",
                    "request_status": childStatus,
                    "error_code": None,
                }
                for index, childStatus in enumerate(childStatuses)
            ],
        }
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, **_kwargs):
        return ReconcileCursor(self.state)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


@pytest.mark.parametrize(
    ("childStatuses", "expected"),
    [
        (("PENDING", "COMPLETED"), "IN_PROGRESS"),
        (("PARTIALLY_FAILED", "COMPLETED"), "PARTIALLY_FAILED"),
        (("COMPLETED", "COMPLETED"), "COMPLETED"),
    ],
)
def test_reconcile_derives_batch_state_and_scrubs_completed_items(
    childStatuses, expected
):
    connection = ReconcileConnection(childStatuses=childStatuses)

    result = _repository(connection).reconcileBatch(BATCH_ID)

    assert result["status"] == expected
    for item, childStatus in zip(result["items"], childStatuses):
        if childStatus == "COMPLETED":
            assert item["target_user_id"] is None
        elif expected != "COMPLETED":
            assert item["target_user_id"] is not None
    if expected == "COMPLETED":
        assert result["reason"] is None
        assert all(item["target_user_id"] is None for item in result["items"])
        assert result["completed_at"] is not None
    else:
        assert result["reason"] == "support request"
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed == 1


def test_reconcile_expires_preview_and_scrubs_all_direct_identifiers():
    connection = ReconcileConnection(
        status="PREVIEWED", childStatuses=(None, None), expired=True
    )

    result = _repository(connection).reconcileBatch(BATCH_ID)

    assert result["status"] == "EXPIRED"
    assert result["reason"] is None
    assert all(item["target_user_id"] is None for item in result["items"])
    assert result["completed_at"] is not None


def test_list_reconcilable_is_bounded_to_expired_or_confirmed_nonterminal_batches():
    connection = ReconcileConnection(claimable=("batch-1", "batch-2"))

    result = _repository(connection).listReconcilable(limit=2)

    assert result == ["batch-1", "batch-2"]
    query, params = connection.state["executed"][0]
    assert "status = 'previewed'" in query and "expires_at <= now()" in query
    assert "status in ('in_progress', 'partially_failed')" in query
    assert "confirmed_at is not null" in query
    assert "order by batch.created_at" in query
    assert "limit %s" in query
    assert params == (2,)
    assert connection.commits == 0
    assert connection.closed == 1


def test_reconcile_rolls_back_and_closes_on_database_failure():
    connection = ReconcileConnection(
        failOn="update public.user_erasure_batches"
    )

    with pytest.raises(RuntimeError, match="password"):
        _repository(connection).reconcileBatch(BATCH_ID)

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed == 1
