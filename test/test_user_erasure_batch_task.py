class FakeService:
    def __init__(self, failBatchId=None):
        self.failBatchId = failBatchId
        self.calls = []

    def listReconcilable(self, limit):
        self.calls.append(("list", limit))
        return ["batch-1", "batch-2", "batch-3"]

    def reconcile(self, batchId):
        self.calls.append(("reconcile", batchId))
        if batchId == self.failBatchId:
            raise RuntimeError("database password must not leak")
        return {"batchId": batchId, "status": "IN_PROGRESS"}


class CapturingLogger:
    def __init__(self):
        self.calls = []

    def error(self, message, *args):
        self.calls.append((message, args))


def _task(service):
    from nubrix.triggers.tasks.userErasureBatchTask import UserErasureBatchTask

    return UserErasureBatchTask(service=service)


def test_reconcile_returns_only_batch_id_and_stable_status():
    service = FakeService()

    result = _task(service).reconcile("batch-1")

    assert result == {"batchId": "batch-1", "status": "IN_PROGRESS"}
    assert service.calls == [("reconcile", "batch-1")]


def test_sweep_reconciles_every_claimable_batch_and_isolates_failures(monkeypatch):
    import nubrix.triggers.tasks.userErasureBatchTask as module

    logger = CapturingLogger()
    monkeypatch.setattr(module, "logger", logger)
    service = FakeService(failBatchId="batch-2")

    result = _task(service).sweep(limit=100)

    assert result == {"examined": 3, "reconciled": 2, "failed": 1}
    assert service.calls == [
        ("list", 100),
        ("reconcile", "batch-1"),
        ("reconcile", "batch-2"),
        ("reconcile", "batch-3"),
    ]
    assert logger.calls == [
        (
            "User erasure batch reconciliation failed: batch={}, code={}",
            ("batch-2", "BATCH_RECONCILE_FAILED"),
        )
    ]
    assert "password" not in repr(result) + repr(logger.calls)


def test_batch_reconciliation_celery_tasks_and_one_minute_schedule_are_registered():
    from nubrix.triggers.celery import celeryApp

    registered = set(celeryApp.tasks)
    assert "NubrixAI.userErasureBatchReconcile" in registered
    assert "NubrixAI.userErasureBatchSweep" in registered

    schedule = celeryApp.conf.beat_schedule[
        "user-erasure-batch-reconciliation-every-minute"
    ]
    assert schedule["task"] == "NubrixAI.userErasureBatchSweep"
    assert schedule["schedule"].minute == set(range(60))
