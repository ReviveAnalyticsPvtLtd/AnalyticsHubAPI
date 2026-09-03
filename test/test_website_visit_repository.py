import datetime

import pytest

from api.services.websiteVisitRepository import WebsiteVisitRepository


class FakeCursor:
    def __init__(self, rows=None, failure=None):
        self.rows = rows or []
        self.failure = failure
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, values=None):
        self.executions.append((query, values))
        if self.failure:
            raise self.failure

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor, commitFailure=None):
        self.cursorValue = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.cursorFactories = []
        self.commitFailure = commitFailure

    def cursor(self, cursor_factory=None):
        self.cursorFactories.append(cursor_factory)
        return self.cursorValue

    def commit(self):
        self.commits += 1
        if self.commitFailure:
            raise self.commitFailure

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_record_visit_uses_atomic_parameterized_insert_and_commits_duplicate():
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    WebsiteVisitRepository(connectionFactory=lambda: connection).recordVisit(
        "session-1", "/", "agent", "127.0.0.1"
    )

    insertQuery, insertValues = cursor.executions[-1]
    assert "on conflict (session_id) do nothing" in insertQuery.lower()
    assert insertValues == ("session-1", "/", "agent", "127.0.0.1")
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True


def test_record_visit_rolls_back_and_closes_when_database_write_fails():
    cursor = FakeCursor(failure=RuntimeError("connection failure"))
    connection = FakeConnection(cursor)

    with pytest.raises(RuntimeError):
        WebsiteVisitRepository(connectionFactory=lambda: connection).recordVisit(
            "session-1", "/", None, None
        )

    assert connection.rollbacks == 1
    assert connection.closed is True


def test_record_visit_rolls_back_and_closes_when_commit_fails():
    connection = FakeConnection(FakeCursor(), commitFailure=RuntimeError("commit failed"))

    with pytest.raises(RuntimeError):
        WebsiteVisitRepository(connectionFactory=lambda: connection).recordVisit(
            "session-1", "/", None, None
        )

    assert connection.commits == 1
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_count_daily_visits_uses_bounded_utc_query_and_returns_plain_dicts():
    start = datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2026, 8, 27, tzinfo=datetime.timezone.utc)
    cursor = FakeCursor(rows=[{"day": start, "visits": 2}])
    connection = FakeConnection(cursor)

    result = WebsiteVisitRepository(connectionFactory=lambda: connection).countDailyVisits(start, end)

    query, values = cursor.executions[-1]
    assert "created_at >= %s" in query
    assert "created_at < %s" in query
    assert values == (start, end)
    assert result == [{"day": start, "visits": 2}]
    assert connection.commits == 1
    assert connection.closed is True


def test_count_daily_visits_rolls_back_and_closes_when_query_fails():
    cursor = FakeCursor(failure=RuntimeError("query failed"))
    connection = FakeConnection(cursor)
    start = datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc)

    with pytest.raises(RuntimeError):
        WebsiteVisitRepository(connectionFactory=lambda: connection).countDailyVisits(
            start, start + datetime.timedelta(days=1)
        )

    assert connection.rollbacks == 1
    assert connection.closed is True
