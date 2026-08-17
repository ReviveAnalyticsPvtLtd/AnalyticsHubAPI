import os

os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nubrix.triggers.tasks.adminSessionCleanupTask import (
    ADMIN_AUDIT_TABLE,
    ADMIN_SESSIONS_TABLE,
    AUDIT_RETENTION_DAYS,
    SESSION_RETENTION_DAYS,
    AdminSessionCleanupTask,
)


FIXED_NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeCleanupQuery:
    def __init__(self, client, tableName):
        self.client = client
        self.tableName = tableName
        self.column = None
        self.cutoff = None

    def delete(self):
        return self

    def lt(self, column, cutoff):
        self.column = column
        self.cutoff = cutoff
        return self

    def execute(self):
        if self.client.failTable == self.tableName:
            raise RuntimeError("sweep failed; secret=must-not-be-logged")
        self.client.deleteCalls.append(
            (self.tableName, self.column, self.cutoff)
        )
        removed = [
            row for row in self.client.rows.get(self.tableName, [])
            if row[self.column] < self.cutoff
        ]
        self.client.rows[self.tableName] = [
            row for row in self.client.rows.get(self.tableName, [])
            if row[self.column] >= self.cutoff
        ]
        return SimpleNamespace(data=removed)


class FakeCleanupClient:
    def __init__(self, failTable=None, rows=None):
        self.rows = rows if rows is not None else {
            ADMIN_SESSIONS_TABLE: [],
            ADMIN_AUDIT_TABLE: [],
        }
        self.failTable = failTable
        self.deleteCalls = []

    def table(self, name):
        return FakeCleanupQuery(self, name)


def buildTask(**kwargs):
    client = FakeCleanupClient(**kwargs)
    return AdminSessionCleanupTask(client=client, now=lambda: FIXED_NOW), client


def test_deletes_sessions_past_the_retention_window():
    task, client = buildTask()

    task.execute()

    cutoff = (FIXED_NOW - timedelta(days=SESSION_RETENTION_DAYS)).isoformat()
    assert (ADMIN_SESSIONS_TABLE, "expires_at", cutoff) in client.deleteCalls


def test_deletes_audit_rows_past_the_retention_window():
    task, client = buildTask()

    task.execute()

    cutoff = (FIXED_NOW - timedelta(days=AUDIT_RETENTION_DAYS)).isoformat()
    assert (ADMIN_AUDIT_TABLE, "created_at", cutoff) in client.deleteCalls


def test_keeps_sessions_inside_the_retention_window():
    recent = (FIXED_NOW - timedelta(days=1)).isoformat()
    stale = (FIXED_NOW - timedelta(days=90)).isoformat()
    task, client = buildTask(rows={
        ADMIN_SESSIONS_TABLE: [
            {"id": "recent", "expires_at": recent},
            {"id": "stale", "expires_at": stale},
        ],
        ADMIN_AUDIT_TABLE: [],
    })

    result = task.execute()

    assert result["sessionsDeleted"] == 1
    remaining = [row["id"] for row in client.rows[ADMIN_SESSIONS_TABLE]]
    assert remaining == ["recent"]


def test_keeps_audit_rows_that_outlive_sessions():
    aged = (FIXED_NOW - timedelta(days=90)).isoformat()
    task, client = buildTask(rows={
        ADMIN_SESSIONS_TABLE: [],
        ADMIN_AUDIT_TABLE: [{"id": "audit-1", "created_at": aged}],
    })

    result = task.execute()

    assert result["auditRowsDeleted"] == 0
    assert len(client.rows[ADMIN_AUDIT_TABLE]) == 1


def test_audit_sweep_runs_even_when_the_session_sweep_fails():
    task, client = buildTask(failTable=ADMIN_SESSIONS_TABLE)

    result = task.execute()

    assert result["sessionsDeleted"] == 0
    assert any(call[0] == ADMIN_AUDIT_TABLE for call in client.deleteCalls)


def test_session_sweep_runs_even_when_the_audit_sweep_fails():
    task, client = buildTask(failTable=ADMIN_AUDIT_TABLE)

    result = task.execute()

    assert result["auditRowsDeleted"] == 0
    assert any(call[0] == ADMIN_SESSIONS_TABLE for call in client.deleteCalls)


def test_sweep_failure_does_not_leak_backend_detail():
    from loguru import logger

    task, _ = buildTask(failTable=ADMIN_SESSIONS_TABLE)
    captured = []
    sinkId = logger.add(captured.append, level="WARNING")
    try:
        task.execute()
    finally:
        logger.remove(sinkId)

    assert "must-not-be-logged" not in "".join(captured)


def test_audit_retention_is_longer_than_session_retention():
    assert AUDIT_RETENTION_DAYS > SESSION_RETENTION_DAYS


def test_celery_registers_the_cleanup_task_on_a_daily_schedule():
    from nubrix.triggers.celery import celeryApp

    schedule = celeryApp.conf.beat_schedule["admin-session-cleanup-daily"]

    assert schedule["task"] == "NubrixAI.adminSessionCleanup"
    assert schedule["schedule"].hour == {3}
    assert schedule["schedule"].minute == {0}
