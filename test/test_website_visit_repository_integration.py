"""Opt-in disposable PostgreSQL checks for page_visits semantics and privileges."""

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest
from psycopg2.errors import InsufficientPrivilege

from api.services.websiteVisitRepository import WebsiteVisitRepository
from test.website_visit_test_support import requireSafeWebsiteVisitTestDatabaseUrl


TEST_DATABASE_URL = os.environ.get("WEBSITE_VISIT_TEST_DATABASE_URL")
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/20260903195521_create_page_visits.sql"
)


@pytest.fixture(scope="module", autouse=True)
def disposableDatabase():
    if not TEST_DATABASE_URL:
        pytest.skip("WEBSITE_VISIT_TEST_DATABASE_URL is not configured")
    try:
        requireSafeWebsiteVisitTestDatabaseUrl(TEST_DATABASE_URL)
    except ValueError as exc:
        pytest.fail(str(exc))
    connection = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass('public.page_visits')")
            exists = cursor.fetchone()[0] is not None
            if not exists:
                cursor.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


def repository() -> WebsiteVisitRepository:
    return WebsiteVisitRepository(connectionFactory=lambda: psycopg2.connect(TEST_DATABASE_URL))


def deleteSessions(sessionIds):
    connection = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute("delete from public.page_visits where session_id = any(%s::uuid[])", (list(sessionIds),))
        connection.commit()
    finally:
        connection.close()


def test_concurrent_duplicate_reports_create_one_row_and_preserve_first_metadata():
    sessionId = str(uuid.uuid4())
    start = threading.Barrier(2)
    failures = []

    def record(path, agent):
        try:
            start.wait()
            repository().recordVisit(sessionId, path, agent, "127.0.0.1")
        except Exception as exc:  # pragma: no cover - assertion below reports it
            failures.append(exc)

    first = threading.Thread(target=record, args=("/first", "first-agent"))
    second = threading.Thread(target=record, args=("/second", "second-agent"))
    first.start()
    second.start()
    first.join()
    second.join()

    try:
        assert failures == []
        connection = psycopg2.connect(TEST_DATABASE_URL)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select count(*), min(created_at), min(path), min(user_agent) "
                    "from public.page_visits where session_id = %s",
                    (sessionId,),
                )
                count, createdAt, path, userAgent = cursor.fetchone()
        finally:
            connection.close()
        assert count == 1
        assert createdAt is not None
        assert (path, userAgent) in {
            ("/first", "first-agent"), ("/second", "second-agent"),
        }
    finally:
        deleteSessions([sessionId])


def test_duplicate_does_not_change_the_first_timestamp_or_metadata():
    sessionId = str(uuid.uuid4())
    try:
        repo = repository()
        repo.recordVisit(sessionId, "/first", "first-agent", "127.0.0.1")
        connection = psycopg2.connect(TEST_DATABASE_URL)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select created_at from public.page_visits where session_id = %s",
                    (sessionId,),
                )
                firstTimestamp = cursor.fetchone()[0]
        finally:
            connection.close()
        repo.recordVisit(sessionId, "/later", "later-agent", "::1")

        connection = psycopg2.connect(TEST_DATABASE_URL)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select created_at, path, user_agent, host(ip_address) "
                    "from public.page_visits where session_id = %s",
                    (sessionId,),
                )
                createdAt, path, agent, address = cursor.fetchone()
        finally:
            connection.close()
        assert createdAt == firstTimestamp
        assert (path, agent, address) == ("/first", "first-agent", "127.0.0.1")
    finally:
        deleteSessions([sessionId])


def test_daily_counts_group_by_utc_day_with_exclusive_range_end():
    sessionIds = [str(uuid.uuid4()) for _ in range(3)]
    start = datetime(2040, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    connection = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            for sessionId, createdAt in zip(sessionIds, [
                start,
                start + timedelta(days=1, hours=5),
                end,
            ]):
                cursor.execute(
                    "insert into public.page_visits (session_id, path, created_at) values (%s, %s, %s)",
                    (sessionId, "/", createdAt),
                )
        connection.commit()
        rows = repository().countDailyVisits(start, end)
        assert rows == [
            {"day": start, "visits": 1},
            {"day": start + timedelta(days=1), "visits": 1},
        ]
    finally:
        connection.close()
        deleteSessions(sessionIds)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_public_roles_have_no_page_visit_privileges(role):
    connection = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"set role {role}")
            with pytest.raises(InsufficientPrivilege):
                cursor.execute("select count(*) from public.page_visits")
        connection.rollback()
    finally:
        connection.close()


def test_service_role_can_insert_and_select_page_visits():
    sessionId = str(uuid.uuid4())
    connection = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute("set role service_role")
            cursor.execute(
                "insert into public.page_visits (session_id, path) values (%s, %s)",
                (sessionId, "/service-role"),
            )
            cursor.execute(
                "select path from public.page_visits where session_id = %s",
                (sessionId,),
            )
            assert cursor.fetchone()[0] == "/service-role"
        connection.commit()
    finally:
        connection.close()
        deleteSessions([sessionId])
