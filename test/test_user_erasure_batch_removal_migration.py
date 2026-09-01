from pathlib import Path


def _removalMigration() -> str:
    matches = list(
        Path("supabase/migrations").glob("*_remove_user_erasure_batches.sql")
    )
    assert len(matches) == 1
    return " ".join(matches[0].read_text(encoding="utf-8").lower().split())


def test_removal_migration_drops_batch_schema_in_dependency_order():
    sql = _removalMigration()
    dropItems = "drop table if exists public.user_erasure_batch_items;"
    dropBatches = "drop table if exists public.user_erasure_batches;"
    dropIndex = (
        "drop index if exists "
        "public.user_erasure_requests_subject_fingerprint_idx;"
    )

    assert dropItems in sql
    assert dropBatches in sql
    assert dropIndex in sql
    assert sql.index(dropItems) < sql.index(dropBatches)


def test_removal_migration_preserves_single_user_erasure_schema():
    sql = _removalMigration()

    assert "drop table if exists public.user_erasure_requests" not in sql
    assert "drop table if exists public.user_erasure_steps" not in sql
    assert "drop column" not in sql
