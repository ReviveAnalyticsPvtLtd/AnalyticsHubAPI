"""Opt-in regression coverage for page_visits migration privileges."""

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import pytest

from test.website_visit_test_support import requireSafeWebsiteVisitTestDatabaseUrl


TEST_DATABASE_URL = os.environ.get("WEBSITE_VISIT_TEST_DATABASE_URL")
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/20260903195521_create_page_visits.sql"
)


def _migrationTestDatabaseUrl() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("WEBSITE_VISIT_TEST_DATABASE_URL is not configured")
    parsed = urlparse(TEST_DATABASE_URL)
    try:
        requireSafeWebsiteVisitTestDatabaseUrl(TEST_DATABASE_URL)
    except ValueError:
        pytest.skip("migration ACL regression requires website_visit_migration_test on loopback")
    if parsed.path.lstrip("/") != "website_visit_migration_test":
        pytest.skip("migration ACL regression requires website_visit_migration_test on loopback")
    return TEST_DATABASE_URL


def test_migration_revokes_representative_default_service_role_privileges():
    """A narrow GRANT must remove default UPDATE/DELETE rights, not add to them."""
    connection = psycopg2.connect(_migrationTestDatabaseUrl())
    try:
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass('public.page_visits')")
            assert cursor.fetchone()[0] is None
            cursor.execute(
                "alter default privileges in schema public "
                "grant all on tables to service_role"
            )
            cursor.execute(MIGRATION_PATH.read_text(encoding="utf-8"))
            cursor.execute(
                "select privilege_type from information_schema.role_table_grants "
                "where grantee = 'service_role' and table_schema = 'public' "
                "and table_name = 'page_visits' order by privilege_type"
            )
            privileges = {row[0] for row in cursor.fetchall()}

        assert privileges == {"INSERT", "SELECT"}
    finally:
        connection.rollback()
        connection.close()
