from datetime import datetime, timezone
from unittest.mock import patch

from nubrix.triggers.tasks.adminTrialCreditSyncTask import AdminTrialCreditSyncTask


ITEM = {
    "id": "item-1",
    "user_id": "free-user",
    "outcome": "EXTENDED",
    "credit_sync_status": "PENDING",
    "credit_quota": 1_000,
    "credit_topup_tokens": 250,
    "credit_period_end": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
    "credit_generation": 4,
}


class FakeRepository:
    def __init__(self, item=ITEM):
        self.item = dict(item) if item else None
        self.marked = []

    def getItemById(self, _itemId):
        return dict(self.item) if self.item else None

    def listPendingCreditSync(self, limit=100):
        return [dict(self.item)] if self.item else []

    def synchronizeCreditItem(self, itemId, syncCallback):
        if self.item is None or self.item["id"] != itemId:
            return None
        result = syncCallback(dict(self.item))
        if result == "APPLIED":
            self.marked.append(itemId)
            self.item["credit_sync_status"] = "SYNCED"
        elif result == "STALE":
            self.item["credit_sync_status"] = "SUPERSEDED"
        return dict(self.item)


class FakeCredits:
    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.calls = []

    def refreshTrialCreditsCache(self, **kwargs):
        self.calls.append(kwargs)
        return "APPLIED" if self.succeeds else "FAILED"


def test_sync_task_marks_item_synced_after_cache_publish():
    repository = FakeRepository()
    credits = FakeCredits()

    result = AdminTrialCreditSyncTask(repository, credits).execute("item-1")

    assert result == {"itemId": "item-1", "status": "SYNCED"}
    assert repository.marked == ["item-1"]
    assert credits.calls[0]["topupTokens"] == 250


def test_sync_task_leaves_item_pending_when_redis_is_unavailable():
    repository = FakeRepository()

    result = AdminTrialCreditSyncTask(
        repository, FakeCredits(succeeds=False)
    ).execute("item-1")

    assert result == {"itemId": "item-1", "status": "PENDING"}
    assert repository.marked == []


def test_sweep_retries_pending_items():
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
