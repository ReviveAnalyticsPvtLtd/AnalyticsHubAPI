"""
Celery application setup for AnalyticsHub.

This module defines a Celery application wrapper for managing background tasks,
specifically for generating and sending forecasts. It configures the Celery app
with Redis as the broker and backend, and registers the available tasks.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["celeryApp"]

from analyticsHub.triggers.tasks.pastDueSuspensionTask import PastDueSuspensionTask
from analyticsHub.triggers.tasks.generateForecasts import GenerateForecasts
from analyticsHub.triggers.tasks.renewalLifecycleTask import RenewalLifecycleTask
from analyticsHub.triggers.tasks.annualRenewalTask import AnnualRenewalTask
from analyticsHub.triggers.tasks.reconciliationTask import ReconciliationTask
from analyticsHub.triggers.tasks.billingTask import DailyBillingTask
from celery.schedules import crontab
from celery import Celery
import os

class CeleryWrapper:
    """
    Wrapper class for initializing and configuring a Celery application.

    Attributes:
        name (str): The name of the Celery application.
        _app (Celery): The Celery application instance.

    Methods:
        redisUrl: Returns the Redis URL for broker and backend.
        app: Returns the Celery application instance.
        _registerTasks: Registers Celery tasks with the application.
    """
    def __init__(self, name: str):
        """
        Initialize the CeleryWrapper with the given application name.

        Args:
            name (str): The name to assign to the Celery application.
        """
        self.name = name
        self._app = Celery(
            self.name,
            broker = self.redisUrl,
            backend = self.redisUrl
        )
        self._registerTasks()

    @property
    def redisUrl(self) -> str:
        """
        Construct the Redis URL using environment variables.

        Returns:
            str: The Redis connection URL for Celery broker and backend.
        """
        return f'redis://default:{os.environ.get("REDIS_PASSWORD")}@{os.environ.get("REDIS_HOST")}:{os.environ.get("REDIS_PORT")}'

    @property
    def app(self):
        """
        Get the Celery application instance.

        Returns:
            Celery: The configured Celery application.
        """
        return self._app

    def _registerTasks(self):
        """
        Register all Celery tasks and configure the Beat schedule.

        Task schedule:
            - 00:00 UTC daily: Monthly recurring charges.
            - 00:30 UTC daily: Annual renewal T-30/T-7 sweep + emails.
            - 02:00 UTC daily: Renewal reminder emails (T-1, due-today).
            - Every 30 min: Past-due and suspension transitions.
            - Every 15 min: Reconciliation sweep.
        """
        @self._app.task(name=f"{self.name}.generateForecasts")
        def sendForecasts():
            return GenerateForecasts().generateAndSendForecasts()

        @self._app.task(name=f"{self.name}.dailyBilling")
        def runDailyBilling():
            return DailyBillingTask().execute()

        @self._app.task(name=f"{self.name}.annualRenewal")
        def runAnnualRenewal():
            return AnnualRenewalTask().execute()

        @self._app.task(name=f"{self.name}.renewalLifecycle")
        def runRenewalLifecycle():
            return RenewalLifecycleTask().execute()

        @self._app.task(name=f"{self.name}.pastDueSuspension")
        def runPastDueSuspension():
            return PastDueSuspensionTask().execute()

        @self._app.task(name=f"{self.name}.reconciliation")
        def runReconciliation():
            return ReconciliationTask().execute()

        self._app.conf.beat_schedule = {
            "daily-billing-midnight": {
                "task": f"{self.name}.dailyBilling",
                "schedule": crontab(minute=0, hour=0),
            },
            "annual-renewal-daily": {
                "task": f"{self.name}.annualRenewal",
                "schedule": crontab(minute=30, hour=0),
            },
            "renewal-reminders-daily": {
                "task": f"{self.name}.renewalLifecycle",
                "schedule": crontab(minute=0, hour=2),
            },
            "past-due-suspension-every-30min": {
                "task": f"{self.name}.pastDueSuspension",
                "schedule": crontab(minute="*/30"),
            },
            "reconciliation-every-15min": {
                "task": f"{self.name}.reconciliation",
                "schedule": crontab(minute="*/15"),
            },
        }
        self._app.conf.timezone = "UTC"

celeryApp = CeleryWrapper("AnalyticsHub").app