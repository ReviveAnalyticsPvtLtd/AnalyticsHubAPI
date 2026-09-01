from pathlib import Path


def _migration_sql() -> str:
    migrations = list(
        Path("supabase/migrations").glob(
            "*_simplify_admin_trial_extensions.sql"
        )
    )
    assert len(migrations) == 1
    return migrations[0].read_text(encoding="utf-8").lower()


def test_migration_replaces_batch_tables_with_one_extension_table():
    sql = _migration_sql()

    create = "create table public.admin_free_trial_extensions"
    drop_items = "drop table if exists public.admin_free_trial_extension_items"
    drop_batches = "drop table if exists public.admin_free_trial_extension_batches"

    assert create in sql
    assert drop_items in sql
    assert drop_batches in sql
    assert sql.index(create) < sql.index(drop_items) < sql.index(drop_batches)


def test_single_extension_table_keeps_idempotency_credit_outbox_and_rls():
    sql = _migration_sql()

    assert "idempotency_key uuid not null unique" in sql
    assert "user_id text not null" in sql
    assert "credit_sync_status text not null" in sql
    assert "where credit_sync_status = 'pending'" in sql
    assert "alter table public.admin_free_trial_extensions enable row level security" in sql
    assert "from anon, authenticated" in sql
    assert "to service_role" in sql


def test_credit_reconciliation_is_repointed_before_old_tables_are_dropped():
    sql = _migration_sql()
    function_start = sql.index(
        "create or replace function public.reconcile_credit_balance_if_no_admin_refresh"
    )
    function_end = sql.index("$$;", function_start)
    function_sql = sql[function_start:function_end]

    assert "from public.admin_free_trial_extensions" in function_sql
    assert "admin_free_trial_extension_items" not in function_sql
    assert function_end < sql.index(
        "drop table if exists public.admin_free_trial_extension_items"
    )
