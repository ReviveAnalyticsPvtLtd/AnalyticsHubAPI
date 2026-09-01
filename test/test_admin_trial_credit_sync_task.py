from datetime import datetime, timezone
from unittest.mock import patch

from nubrix.triggers.tasks.adminTrialCreditSyncTask import AdminTrialCreditSyncTask


EXTENSION = {
    "id": "extension-1",
    "user_id": "free-user",
    "outcome": "EXTENDED",
    "credit_sync_status": "PENDING",
    "credit_quota": 1_000,
    "credit_topup_tokens": 250,
    "credit_period_end": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
    "credit_generation": 4,
}


class FakeRepository:
    def __init__(self, extension=EXTENSION):
        self.extension = dict(extension) if extension else None
        self.marked = []

    def getExtensionById(self, _extensionId):
        return dict(self.extension) if self.extension else None

    def listPendingCreditSync(self, limit=100):
        return [dict(self.extension)] if self.extension else []

    def synchronizeCreditExtension(self, extensionId, syncCallback):
        if self.extension is None or self.extension["id"] != extensionId:
            return None
        result = syncCallback(dict(self.extension))
        if result == "APPLIED":
            self.marked.append(extensionId)
            self.extension["credit_sync_status"] = "SYNCED"
        elif result == "STALE":
            self.extension["credit_sync_status"] = "SUPERSEDED"
        return dict(self.extension)


class FakeCredits:
    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.calls = []

    def refreshTrialCreditsCache(self, **kwargs):
        self.calls.append(kwargs)
        return "APPLIED" if self.succeeds else "FAILED"


def test_sync_task_marks_extension_synced_after_cache_publish():
    repository = FakeRepository()
    credits = FakeCredits()

    result = AdminTrialCreditSyncTask(repository, credits).execute("extension-1")

    assert result == {"extensionId": "extension-1", "status": "SYNCED"}
    assert repository.marked == ["extension-1"]
    assert credits.calls[0]["topupTokens"] == 250


def test_sync_task_leaves_extension_pending_when_redis_is_unavailable():
    repository = FakeRepository()

    result = AdminTrialCreditSyncTask(
        repository, FakeCredits(succeeds=False)
    ).execute("extension-1")

    assert result == {"extensionId": "extension-1", "status": "PENDING"}
    assert repository.marked == []


def test_sweep_retries_pending_extensions():
    repository = FakeRepository()

    result = AdminTrialCreditSyncTask(repository, FakeCredits()).sweep()

    assert result == {"checked": 1, "synced": 1, "pending": 0}


def test_credit_reconciliation_delegates_atomic_guard_to_credit_service():
    from nubrix.triggers.tasks import creditReconciliationTask as module

    class Result:
        data = [{"user_id": "pending-user"}, {"user_id": "normal-user"}]

    class Query:
        def select(self, *_args):
            return self

        def gt(self, *_args):
            return self

        def execute(self):
            return Result()

    class Client:
        def table(self, _name):
            return Query()

    reconciled = []

    class Credits:
        def reconcile(self, userId):
            reconciled.append(userId)

    with patch.object(module, "sweepStuckTopupPurchases", return_value={
        "checked": 0, "granted": 0
    }):
        result = module.CreditReconciliationTask(
            client=Client(), creditService=Credits()
        ).execute()

    assert reconciled == ["pending-user", "normal-user"]
    assert result["reconciledCount"] == 2
