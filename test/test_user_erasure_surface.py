from fastapi.routing import APIRoute

from main import app
from nubrix.triggers.celery import celeryApp


def test_admin_exposes_exactly_one_erasure_endpoint():
    routes = {
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute)
        and "erasure" in route.path
        for method in route.methods
    }

    assert routes == {("/admin/users/{userId}/erasure", "POST")}


def test_celery_registers_no_batch_erasure_tasks_or_schedule():
    taskNames = set(celeryApp.tasks)
    scheduleTasks = {
        entry["task"] for entry in celeryApp.conf.beat_schedule.values()
    }

    assert "NubrixAI.userErasure" in taskNames
    assert "NubrixAI.userErasureSweep" in taskNames
    assert not any("userErasureBatch" in name for name in taskNames)
    assert not any("userErasureBatch" in name for name in scheduleTasks)
