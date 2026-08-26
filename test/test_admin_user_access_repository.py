import pytest

from api.adminErrors import AdminApiError


class RestoreCursor:
    def __init__(self, state):
        self.state = state
        self.current = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split()).lower()
        self.state["executed"].append((normalized, params))
        self.current = None
        self.rowcount = 0
        if "pg_advisory_xact_lock" in normalized:
            if self.state.get("erasureStartsAfterLock"):
                self.state["erasurePending"] = True
            return
        if 'from public."users"' in normalized:
            self.current = dict(self.state["user"]) if self.state["user"] else None
        elif "from public.subscriptions" in normalized:
            self.current = (
                {"erasure_pending": True}
                if self.state["erasurePending"]
                else None
            )
        elif "from public.user_erasure_requests" in normalized:
            self.current = (
                {"id": "request-active"}
                if self.state["activeRequest"]
                else None
            )
        elif 'delete from public."sessions"' in normalized:
            if self.state.get("failSessionDelete"):
                raise RuntimeError("session database password must not leak")
            self.rowcount = self.state["sessionCount"]
        elif 'update public."users"' in normalized:
            if self.state.get("failUserUpdate"):
                raise RuntimeError("user database password must not leak")
            self.state["user"].update({
                "isBanned": False,
                "bannedAt": None,
                "bannedBy": None,
                "banReason": None,
            })
            self.current = dict(self.state["user"])
            self.rowcount = 1

    def fetchone(self):
        return self.current


class RestoreConnection:
    def __init__(
        self,
        *,
        user=None,
        erasurePending=False,
        activeRequest=False,
        sessionCount=2,
        erasureStartsAfterLock=False,
        failSessionDelete=False,
        failUserUpdate=False,
    ):
        self.state = {
            "executed": [],
            "user": user if user is not None else {
                "userId": "user-1",
                "isBanned": True,
                "bannedAt": "2026-08-20T00:00:00+00:00",
                "bannedBy": "admin-1",
                "banReason": "support",
            },
            "erasurePending": erasurePending,
            "activeRequest": activeRequest,
            "sessionCount": sessionCount,
            "erasureStartsAfterLock": erasureStartsAfterLock,
            "failSessionDelete": failSessionDelete,
            "failUserUpdate": failUserUpdate,
        }
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, **_kwargs):
        return RestoreCursor(self.state)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def _repository(connection):
    from api.services.adminUserAccessRepository import AdminUserAccessRepository

    return AdminUserAccessRepository(connectionFactory=lambda: connection)


def test_restore_locks_revalidates_and_clears_sessions_and_ban_in_one_transaction():
    connection = RestoreConnection(sessionCount=2)

    result = _repository(connection).restoreUserAccess("user-1")

    sql = [query for query, _params in connection.state["executed"]]
    lockIndex = next(i for i, query in enumerate(sql) if "pg_advisory_xact_lock" in query)
    userIndex = next(i for i, query in enumerate(sql) if 'from public."users"' in query)
    subscriptionIndex = next(i for i, query in enumerate(sql) if "from public.subscriptions" in query)
    requestIndex = next(i for i, query in enumerate(sql) if "from public.user_erasure_requests" in query)
    deleteIndex = next(i for i, query in enumerate(sql) if 'delete from public."sessions"' in query)
    updateIndex = next(i for i, query in enumerate(sql) if 'update public."users"' in query)
    assert lockIndex < userIndex < subscriptionIndex < requestIndex < deleteIndex < updateIndex
    assert connection.state["executed"][lockIndex][1] == ("user-1",)
    assert "for update" in sql[userIndex]
    assert "erasure_pending = true" in sql[subscriptionIndex]
    assert "status <> 'completed'" in sql[requestIndex]
    assert result == {
        "userId": "user-1",
        "isBanned": False,
        "bannedAt": None,
        "bannedBy": None,
        "banReason": None,
        "sessionsRevoked": 2,
    }
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"erasurePending": True},
        {"activeRequest": True},
        {"erasureStartsAfterLock": True},
    ],
)
def test_restore_rejects_erasure_state_revalidated_after_advisory_lock(overrides):
    connection = RestoreConnection(**overrides)

    with pytest.raises(AdminApiError) as captured:
        _repository(connection).restoreUserAccess("user-1")

    assert (captured.value.statusCode, captured.value.message) == (
        409,
        "User erasure is in progress",
    )
    writes = [
        query for query, _params in connection.state["executed"]
        if query.startswith(("insert ", "update ", "delete "))
    ]
    assert writes == []
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.state["user"]["isBanned"] is True


def test_restore_rolls_back_when_session_deletion_fails_before_unban():
    connection = RestoreConnection(failSessionDelete=True)

    with pytest.raises(Exception) as captured:
        _repository(connection).restoreUserAccess("user-1")

    assert getattr(captured.value, "stage", None) == "session_revocation"
    assert connection.state["user"]["isBanned"] is True
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_restore_is_idempotent_for_already_active_user_without_revoking_sessions():
    connection = RestoreConnection(user={
        "userId": "user-1",
        "isBanned": False,
        "bannedAt": None,
        "bannedBy": None,
        "banReason": None,
    })

    result = _repository(connection).restoreUserAccess("user-1")

    assert result["isBanned"] is False
    assert result["sessionsRevoked"] == 0
    assert not any(
        query.startswith(("update ", "delete "))
        for query, _params in connection.state["executed"]
    )
    assert connection.commits == 1


def test_restore_missing_user_is_safe_404_and_rolls_back():
    connection = RestoreConnection(user={})
    connection.state["user"] = None

    with pytest.raises(AdminApiError) as captured:
        _repository(connection).restoreUserAccess("missing")

    assert (captured.value.statusCode, captured.value.message) == (
        404,
        "User not found",
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1
