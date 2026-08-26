import re
from pathlib import Path


def _readOnlyMatchingMigration(name: str) -> str:
    matches = list(Path("supabase/migrations").glob(f"*_{name}.sql"))
    assert len(matches) == 1
    return matches[0].read_text(encoding="utf-8").lower()


def _constraintValues(sql: str, constraintName: str, columnName: str) -> set[str]:
    match = re.search(
        rf"constraint\s+{constraintName}\s+check\s*\(\s*"
        rf"{columnName}\s+in\s*\((.*?)\)\s*\)",
        sql,
        re.DOTALL,
    )
    assert match is not None
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _serviceRolePrivileges(sql: str, tableName: str) -> set[str]:
    normalized = " ".join(sql.split())
    matches = list(re.finditer(
        rf"grant\s+([a-z,\s]+?)\s+on table public\.{tableName} "
        r"to service_role;",
        normalized,
    ))
    assert matches
    privileges = set()
    for match in matches:
        privileges.update(
            value.strip() for value in match.group(1).split(",")
        )
    return privileges


def _assertServiceRoleResetPrecedesGrants(sql: str, tableName: str) -> None:
    normalized = " ".join(sql.split())
    revoke = f"revoke all on table public.{tableName} from service_role;"
    firstGrant = re.search(
        rf"grant\s+[a-z,\s]+?\s+on table public\.{tableName} "
        r"to service_role;",
        normalized,
    )
    assert revoke in normalized
    assert firstGrant is not None
    assert normalized.index(revoke) < firstGrant.start()


def test_erasure_batch_migration_has_private_ledgers_and_partial_indexes():
    sql = _readOnlyMatchingMigration("create_user_erasure_batches")

    assert "create table public.user_erasure_batches" in sql
    assert "create table public.user_erasure_batch_items" in sql
    assert sql.count("enable row level security") == 2
    assert sql.count("revoke all") >= 2
    assert "where status = 'previewed'" in sql
    assert "where status in ('in_progress', 'partially_failed')" in sql
    assert "unique (batch_id, subject_fingerprint)" in sql


def test_erasure_batch_migration_constrains_ledger_values_and_relationships():
    sql = _readOnlyMatchingMigration("create_user_erasure_batches")

    assert _constraintValues(
        sql, "user_erasure_batches_status_chk", "status"
    ) == {
        "previewed",
        "in_progress",
        "partially_failed",
        "completed",
        "expired",
    }
    assert "request_hash ~ '^[0-9a-f]{64}$'" in sql
    assert "subject_fingerprint ~ '^[0-9a-f]{64}$'" in sql
    assert "reason is null or char_length(reason) <= 1000" in sql
    assert "requested_count between 1 and 25" in sql
    assert "ready_count between 0 and requested_count" in sql
    assert "expires_at > created_at" in sql
    assert "ordinal between 0 and 24" in sql
    assert _constraintValues(
        sql, "user_erasure_batch_items_classification_chk", "classification"
    ) == {
        "ready",
        "already_in_progress",
        "already_completed",
        "user_not_found",
    }
    assert "unique (batch_id, ordinal)" in sql
    assert "references public.admin_users(id)" in sql
    assert "references public.user_erasure_batches(id) on delete cascade" in sql
    assert "references public.user_erasure_requests(id)" in sql
    assert "target_user_id text" in sql
    assert "target_user_id text references" not in sql


def test_erasure_batch_migration_indexes_preview_lookup_paths():
    sql = _readOnlyMatchingMigration("create_user_erasure_batches")
    normalized = " ".join(sql.split())

    assert "on public.user_erasure_batches (requested_by" in sql
    assert "on public.user_erasure_batch_items (batch_id" in sql
    assert "on public.user_erasure_batch_items (request_id)" in sql
    assert "where request_id is not null" in sql
    assert "where classification in ('ready', 'already_in_progress')" in sql
    assert (
        "create index if not exists user_erasure_requests_subject_fingerprint_idx "
        "on public.user_erasure_requests (subject_fingerprint);"
    ) in normalized


def test_erasure_batch_migration_resets_and_grants_exact_service_role_privileges():
    sql = _readOnlyMatchingMigration("create_user_erasure_batches")
    normalized = " ".join(sql.split())

    assert (
        "revoke all on table public.user_erasure_batches from service_role;"
    ) in normalized
    assert (
        "revoke all on table public.user_erasure_batch_items from service_role;"
    ) in normalized
    _assertServiceRoleResetPrecedesGrants(sql, "user_erasure_batches")
    _assertServiceRoleResetPrecedesGrants(sql, "user_erasure_batch_items")
    assert _serviceRolePrivileges(sql, "user_erasure_batches") == {
        "select",
        "insert",
        "update",
    }
    assert _serviceRolePrivileges(sql, "user_erasure_batch_items") == {
        "select",
        "insert",
        "update",
    }
    assert "grant delete" not in sql
    assert " to anon" not in sql
    assert " to authenticated" not in sql
    assert "create policy" not in sql
