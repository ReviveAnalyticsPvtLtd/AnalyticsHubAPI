from datetime import datetime, timezone

from api.services.adminTrialExtensionRepository import (
    AdminTrialExtensionRepository,
    calculateTrialWindow,
)


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
KEY = "f53b33cd-219e-4c70-b5c2-43d956591fa5"


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


def extension(**overrides):
    row = {
        "id": "extension-1",
        "idempotency_key": KEY,
        "request_hash": "a" * 64,
        "user_id": "free-user",
        "subscription_id": None,
        "requested_by": "admin-1",
        "days": 5,
        "reason": None,
        "outcome": "PENDING",
        "days_added": None,
        "previous_expiry": None,
        "new_expiry": None,
        "credit_sync_status": "NOT_APPLICABLE",
        "credit_quota": None,
        "credit_topup_tokens": None,
        "credit_period_end": None,
        "credit_generation": None,
        "access_still_banned": False,
        "error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
        "completed_at": None,
    }
    row.update(overrides)
    return row


def test_trial_window_stacks_active_time_but_restarts_expired_time():
    activeStart, activeEnd = calculateTrialWindow(subscription(), NOW, days=5)
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


def test_create_extension_persists_one_idempotent_operation():
    stored = extension()
    connection = FakeConnection([stored])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)

    result = repository.createOrGetExtension(
        idempotencyKey=KEY,
        requestHash="a" * 64,
        userId="free-user",
        days=5,
        reason=None,
        adminId="admin-1",
    )

    statements = " ".join(query.lower() for query, _ in connection.fakeCursor.executions)
    assert "insert into public.admin_free_trial_extensions" in statements
    assert "admin_free_trial_extension_batches" not in statements
    assert "admin_free_trial_extension_items" not in statements
    assert result == stored
    assert connection.commits == 1


def test_extend_user_commits_subscription_credit_and_single_operation():
    completed = extension(
        subscription_id="subscription-1",
        outcome="EXTENDED",
        days_added=5,
        previous_expiry=datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc),
        new_expiry=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
        credit_sync_status="PENDING",
        credit_quota=1_000,
        credit_topup_tokens=250,
        credit_period_end=datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        credit_generation=8,
        completed_at=NOW,
    )
    connection = FakeConnection([
        extension(),
        {"is_banned": False},
        subscription(),
        {"topup_tokens": 250},
        completed,
    ])
    repository = AdminTrialExtensionRepository(
        connectionFactory=lambda: connection,
        freeQuotaProvider=lambda: 1_000,
    )

    result = repository.extendUser(
        extensionId="extension-1", userId="free-user", days=5, now=NOW
    )

    statements = " ".join(query.lower() for query, _ in connection.fakeCursor.executions)
    statementList = [
        query.lower() for query, _ in connection.fakeCursor.executions
    ]
    advisoryIndex = next(
        index
        for index, query in enumerate(statementList)
        if "pg_advisory_xact_lock" in query
    )
    operationLockIndex = next(
        index
        for index, query in enumerate(statementList)
        if "from public.admin_free_trial_extensions" in query
        and "for update" in query
    )
    assert 'from public."users"' in statements
    assert "from public.subscriptions" in statements
    assert "for update" in statements
    assert "pg_advisory_xact_lock" in statements
    assert "update public.subscriptions" in statements
    assert "insert into public.credit_balances" in statements
    assert "update public.admin_free_trial_extensions" in statements
    assert "admin_free_trial_extension_items" not in statements
    assert advisoryIndex < operationLockIndex
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert result["new_expiry"] == datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
    assert result["credit_quota"] == 1_000
    assert result["credit_sync_status"] == "PENDING"
    assert result["credit_generation"] == 8


def test_completed_operation_is_replayed_without_extending_again():
    completed = extension(outcome="EXTENDED", credit_sync_status="SYNCED")
    connection = FakeConnection([completed])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)

    result = repository.extendUser(
        extensionId="extension-1", userId="free-user", days=5, now=NOW
    )

    statements = " ".join(query.lower() for query, _ in connection.fakeCursor.executions)
    assert result == completed
    assert "update public.subscriptions" not in statements
    assert connection.commits == 1


def test_paid_user_is_recorded_as_failure_without_changing_subscription_or_credit():
    failed = extension(
        user_id="paid-user",
        outcome="FAILED",
        error_code="PAID_SUBSCRIPTION_NOT_ELIGIBLE",
        completed_at=NOW,
    )
    connection = FakeConnection([
        extension(user_id="paid-user"),
        {"is_banned": False},
        subscription(billing_mode="monthly_recurring", plan_type="pro", status="active"),
        failed,
    ])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)

    result = repository.extendUser(
        extensionId="extension-1", userId="paid-user", days=5, now=NOW
    )

    statements = " ".join(query.lower() for query, _ in connection.fakeCursor.executions)
    assert "update public.subscriptions" not in statements
    assert "insert into public.credit_balances" not in statements
    assert result["outcome"] == "FAILED"
    assert result["error_code"] == "PAID_SUBSCRIPTION_NOT_ELIGIBLE"


def test_erasure_pending_user_is_ineligible_even_when_trial_is_free():
    failed = extension(
        user_id="erasing-user",
        outcome="FAILED",
        error_code="USER_ERASURE_PENDING",
        completed_at=NOW,
    )
    connection = FakeConnection([
        extension(user_id="erasing-user"),
        {"is_banned": False},
        subscription(erasure_pending=True),
        failed,
    ])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)

    result = repository.extendUser(
        extensionId="extension-1", userId="erasing-user", days=5, now=NOW
    )

    assert result["error_code"] == "USER_ERASURE_PENDING"


def test_missing_user_is_a_durable_single_operation_failure():
    failed = extension(
        user_id="missing-user",
        outcome="FAILED",
        error_code="USER_NOT_FOUND",
        completed_at=NOW,
    )
    connection = FakeConnection([
        extension(user_id="missing-user"),
        None,
        failed,
    ])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)

    result = repository.extendUser(
        extensionId="extension-1", userId="missing-user", days=5, now=NOW
    )

    assert result["outcome"] == "FAILED"
    assert result["error_code"] == "USER_NOT_FOUND"
    assert connection.commits == 1


def test_older_pending_credit_extension_is_superseded_without_touching_redis():
    pending = extension(
        subscription_id="subscription-1",
        outcome="EXTENDED",
        credit_sync_status="PENDING",
        credit_generation=4,
    )
    updated = {**pending, "credit_sync_status": "SUPERSEDED"}
    connection = FakeConnection([
        {"user_id": "free-user", "subscription_id": "subscription-1"},
        {
            "admin_credit_generation": 5,
            "erasure_pending": False,
            "billing_mode": "none",
            "plan_type": "free",
            "status": "trial",
        },
        pending,
        updated,
    ])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)
    publishes = []

    result = repository.synchronizeCreditExtension(
        "extension-1", lambda value: publishes.append(value) or "APPLIED"
    )

    assert result["credit_sync_status"] == "SUPERSEDED"
    assert publishes == []


def test_erasure_pending_cancels_credit_extension_without_touching_redis():
    pending = extension(
        subscription_id="subscription-1",
        outcome="EXTENDED",
        credit_sync_status="PENDING",
        credit_generation=4,
    )
    updated = {**pending, "credit_sync_status": "CANCELLED"}
    connection = FakeConnection([
        {"user_id": "free-user", "subscription_id": "subscription-1"},
        {
            "admin_credit_generation": 4,
            "erasure_pending": True,
            "billing_mode": "none",
            "plan_type": "free",
            "status": "trial",
        },
        pending,
        updated,
    ])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)
    publishes = []

    result = repository.synchronizeCreditExtension(
        "extension-1", lambda value: publishes.append(value) or "APPLIED"
    )

    assert result["credit_sync_status"] == "CANCELLED"
    assert publishes == []


def test_current_credit_generation_is_published_and_marked_synced():
    pending = extension(
        subscription_id="subscription-1",
        outcome="EXTENDED",
        credit_sync_status="PENDING",
        credit_generation=4,
    )
    updated = {**pending, "credit_sync_status": "SYNCED"}
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
        pending,
        updated,
    ])
    repository = AdminTrialExtensionRepository(connectionFactory=lambda: connection)
    publishes = []

    result = repository.synchronizeCreditExtension(
        "extension-1", lambda value: publishes.append(value) or "APPLIED"
    )

    statements = " ".join(query.lower() for query, _ in connection.fakeCursor.executions)
    assert result["credit_sync_status"] == "SYNCED"
    assert len(publishes) == 1
    assert "pg_advisory_xact_lock" in statements
    assert "from public.subscriptions" in statements
    assert statements.count("for update") >= 2
