from datetime import datetime, timezone

from api.services.adminTrialExtensionRepository import (
    AdminTrialExtensionRepository,
    calculateTrialWindow,
)


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.executions.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class FakeConnection:
    def __init__(self, rows):
        self.fakeCursor = FakeCursor(rows)
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self, **_kwargs):
        return self.fakeCursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def subscription(**overrides):
    row = {
        "id": "subscription-1",
        "billing_mode": "none",
        "plan_type": "free",
        "status": "trial",
        "current_period_start": datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
        "current_period_end": datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc),
        "erasure_pending": False,
        "version": 3,
        "admin_credit_generation": 7,
        "domain_count": 4,
    }
    row.update(overrides)
    return row


def test_trial_window_stacks_active_time_but_restarts_expired_time():
    activeStart, activeEnd = calculateTrialWindow(
        subscription(), NOW, days=5
    )
    expiredStart, expiredEnd = calculateTrialWindow(
        subscription(
            status="expired",
            current_period_end=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        ),
        NOW,
        days=5,
    )

    assert activeStart == datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    assert activeEnd == datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    assert expiredStart == NOW
    assert expiredEnd == datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)


def test_extend_user_locks_subscription_and_commits_subscription_credit_and_item():
    connection = FakeConnection([
        {"is_banned": False},
        subscription(),
        {"topup_tokens": 250},
        {"id": "item-1"},
    ])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )

    item = repository.extendUser(
        batchId="batch-1", userId="free-user", days=5, now=NOW
    )

    statements = " ".join(query.lower() for query, _ in connection.fakeCursor.executions)
    assert 'from public."users"' in statements
    assert "from public.subscriptions" in statements
    assert "for update" in statements
    assert "pg_advisory_xact_lock" in statements
    assert "update public.subscriptions" in statements
    assert "insert into public.credit_balances" in statements
    assert "insert into public.admin_free_trial_extension_items" in statements
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert item["new_expiry"] == datetime(
        2026, 8, 30, 10, 0, tzinfo=timezone.utc
    )
    assert item["credit_quota"] == 1_000
    assert item["credit_topup_tokens"] == 250
    assert item["credit_sync_status"] == "PENDING"
    assert item["credit_generation"] == 8


def test_paid_user_is_recorded_as_failure_without_changing_subscription_or_credit():
    connection = FakeConnection([
        {"is_banned": False},
        subscription(
            billing_mode="monthly_recurring",
            plan_type="pro",
            status="active",
        ),
        {"id": "item-paid"},
    ])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )

    item = repository.extendUser(
        batchId="batch-1", userId="paid-user", days=5, now=NOW
    )

    statements = " ".join(query.lower() for query, _ in connection.fakeCursor.executions)
    assert "update public.subscriptions" not in statements
    assert "insert into public.credit_balances" not in statements
    assert item["outcome"] == "FAILED"
    assert item["error_code"] == "PAID_SUBSCRIPTION_NOT_ELIGIBLE"
    assert connection.commits == 1


def test_erasure_pending_user_is_ineligible_even_when_trial_is_free():
    connection = FakeConnection([
        {"is_banned": False},
        subscription(erasure_pending=True),
        {"id": "item-erasure"},
    ])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )

    item = repository.extendUser(
        batchId="batch-1", userId="erasing-user", days=5, now=NOW
    )

    assert item["error_code"] == "USER_ERASURE_PENDING"


def test_missing_user_is_a_durable_per_user_failure():
    connection = FakeConnection([None, {"id": "item-missing"}])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )

    item = repository.extendUser(
        batchId="batch-1", userId="missing-user", days=5, now=NOW
    )

    assert item["outcome"] == "FAILED"
    assert item["error_code"] == "USER_NOT_FOUND"
    assert connection.commits == 1


def test_older_pending_credit_item_is_superseded_without_touching_redis():
    item = {
        "id": "item-old",
        "user_id": "free-user",
        "subscription_id": "subscription-1",
        "outcome": "EXTENDED",
        "credit_sync_status": "PENDING",
        "credit_generation": 4,
    }
    updated = {**item, "credit_sync_status": "SUPERSEDED"}
    connection = FakeConnection([
        {"user_id": "free-user", "subscription_id": "subscription-1"},
        {
            "admin_credit_generation": 5,
            "erasure_pending": False,
            "billing_mode": "none",
            "plan_type": "free",
            "status": "trial",
        },
        item,
        updated,
    ])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )
    publishes = []

    result = repository.synchronizeCreditItem(
        "item-old", lambda value: publishes.append(value) or "APPLIED"
    )

    assert result["credit_sync_status"] == "SUPERSEDED"
    assert publishes == []
    assert connection.commits == 1


def test_erasure_pending_cancels_credit_item_without_touching_redis():
    item = {
        "id": "item-1",
        "user_id": "free-user",
        "subscription_id": "subscription-1",
        "outcome": "EXTENDED",
        "credit_sync_status": "PENDING",
        "credit_generation": 4,
    }
    updated = {**item, "credit_sync_status": "CANCELLED"}
    connection = FakeConnection([
        {"user_id": "free-user", "subscription_id": "subscription-1"},
        {
            "admin_credit_generation": 4,
            "erasure_pending": True,
            "billing_mode": "none",
            "plan_type": "free",
            "status": "trial",
        },
        item,
        updated,
    ])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )
    publishes = []

    result = repository.synchronizeCreditItem(
        "item-1", lambda value: publishes.append(value) or "APPLIED"
    )

    assert result["credit_sync_status"] == "CANCELLED"
    assert publishes == []


def test_unrelated_subscription_version_change_does_not_supersede_credit_reset():
    item = {
        "id": "item-1",
        "user_id": "free-user",
        "subscription_id": "subscription-1",
        "outcome": "EXTENDED",
        "credit_sync_status": "PENDING",
        "credit_generation": 4,
    }
    updated = {**item, "credit_sync_status": "SYNCED"}
    connection = FakeConnection([
        {"user_id": "free-user", "subscription_id": "subscription-1"},
        {
            "version": 99,
            "admin_credit_generation": 4,
            "erasure_pending": False,
            "billing_mode": "none",
            "plan_type": "free",
            "status": "trial",
        },
        item,
        updated,
    ])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )
    publishes = []

    result = repository.synchronizeCreditItem(
        "item-1", lambda value: publishes.append(value) or "APPLIED"
    )

    statements = " ".join(
        query.lower() for query, _ in connection.fakeCursor.executions
    )
    assert result["credit_sync_status"] == "SYNCED"
    assert len(publishes) == 1
    assert "pg_advisory_xact_lock" in statements
    assert "from public.subscriptions" in statements
    assert statements.count("for update") >= 2
