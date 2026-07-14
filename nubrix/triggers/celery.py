"""
celery.py

Celery application + beat schedule for NubrixAI billing/subscription/credit/cron jobs.
All times are UTC. See the schedule dict below for exact cadences.
"""

__all__ = ["celeryApp"]


from nubrix.triggers.tasks.pastDueSuspensionTask import PastDueSuspensionTask
from nubrix.triggers.tasks.generateForecasts import GenerateForecasts
from nubrix.triggers.tasks.renewalLifecycleTask import RenewalLifecycleTask
from nubrix.triggers.tasks.annualRenewalTask import AnnualRenewalTask
from nubrix.triggers.tasks.reconciliationTask import ReconciliationTask
from nubrix.triggers.tasks.billingMetricsTask import BillingMetricsTask
from nubrix.triggers.tasks.entitlementBoundaryTask import EntitlementBoundaryTask
from nubrix.triggers.tasks.subscriptionExpiryTask import SubscriptionExpiryTask
from nubrix.triggers.tasks.creditReconciliationTask import CreditReconciliationTask
from nubrix.triggers.tasks.syncSessionActivityTask import SyncSessionActivityTask
from nubrix.triggers.tasks.billingTask import DailyBillingTask
from nubrix.triggers.tasks.generateReportTask import GenerateReportTask
from nubrix.triggers.tasks.generateMetadataTask import GenerateMetadataTask
from nubrix.triggers.tasks.generateInsightsTask import GenerateInsightsTask
from celery.schedules import crontab
from celery import Celery
import os

APP_NAME = "NubrixAI"
_redisUrl = f'redis://default:{os.environ.get("REDIS_PASSWORD")}@{os.environ.get("REDIS_HOST")}:{os.environ.get("REDIS_PORT")}'

celeryApp = Celery(APP_NAME, broker=_redisUrl, backend=_redisUrl)


@celeryApp.task(name=f"{APP_NAME}.generateForecasts")
def sendForecasts():
    return GenerateForecasts().generateAndSendForecasts()

@celeryApp.task(name=f"{APP_NAME}.dailyBilling")
def runDailyBilling():
    return DailyBillingTask().execute()

@celeryApp.task(name=f"{APP_NAME}.annualRenewal")
def runAnnualRenewal():
    return AnnualRenewalTask().execute()

@celeryApp.task(name=f"{APP_NAME}.renewalLifecycle")
def runRenewalLifecycle():
    return RenewalLifecycleTask().execute()

@celeryApp.task(name=f"{APP_NAME}.pastDueSuspension")
def runPastDueSuspension():
    return PastDueSuspensionTask().execute()

@celeryApp.task(name=f"{APP_NAME}.entitlementBoundary")
def runEntitlementBoundary():
    return EntitlementBoundaryTask().execute()

@celeryApp.task(name=f"{APP_NAME}.reconciliation")
def runReconciliation():
    return ReconciliationTask().execute()

@celeryApp.task(name=f"{APP_NAME}.billingMetrics")
def runBillingMetrics():
    return BillingMetricsTask().execute()

@celeryApp.task(name=f"{APP_NAME}.subscriptionExpiry")
def runSubscriptionExpiry():
    return SubscriptionExpiryTask().execute()

@celeryApp.task(name=f"{APP_NAME}.creditReconciliation")
def runCreditReconciliation():
    return CreditReconciliationTask().execute()

@celeryApp.task(name=f"{APP_NAME}.syncSessionActivity")
def runSyncSessionActivity():
    return SyncSessionActivityTask().execute()

@celeryApp.task(name=f"{APP_NAME}.generateReport")
def runGenerateReport(projectId: str):
    return GenerateReportTask().execute(projectId=projectId)

@celeryApp.task(name=f"{APP_NAME}.generateMetadata")
def runGenerateMetadata(projectId: str, userId: str):
    return GenerateMetadataTask().execute(projectId=projectId, userId=userId)

@celeryApp.task(name=f"{APP_NAME}.generateInsights")
def runGenerateInsights(projectId: str, preserveCharted: bool, userId: str):
    return GenerateInsightsTask().execute(projectId=projectId, preserveCharted=preserveCharted, userId=userId)


celeryApp.conf.beat_schedule = {
    "daily-billing-midnight": {"task": f"{APP_NAME}.dailyBilling", "schedule": crontab(minute=0, hour=0)},
    "annual-renewal-daily": {"task": f"{APP_NAME}.annualRenewal", "schedule": crontab(minute=30, hour=0)},
    "renewal-reminders-daily": {"task": f"{APP_NAME}.renewalLifecycle", "schedule": crontab(minute=0, hour=2)},
    "past-due-suspension-every-30min": {"task": f"{APP_NAME}.pastDueSuspension", "schedule": crontab(minute="*/30")},
    "entitlement-boundary-every-30min": {"task": f"{APP_NAME}.entitlementBoundary", "schedule": crontab(minute="*/30")},
    "reconciliation-every-15min": {"task": f"{APP_NAME}.reconciliation", "schedule": crontab(minute="*/15")},
    "billing-metrics-every-30min": {"task": f"{APP_NAME}.billingMetrics", "schedule": crontab(minute="*/30")},
    "subscription-expiry-daily": {"task": f"{APP_NAME}.subscriptionExpiry", "schedule": crontab(minute=0, hour=1)},
    "credit-reconciliation-hourly": {"task": f"{APP_NAME}.creditReconciliation", "schedule": crontab(minute=0)},
    "sync-session-activity-every-minute": {"task": f"{APP_NAME}.syncSessionActivity", "schedule": crontab(minute="*/1")},
}
celeryApp.conf.timezone = "UTC"

celeryApp.conf.task_routes = {
    f"{APP_NAME}.generateReport": {"queue": "compute"},
    f"{APP_NAME}.generateMetadata": {"queue": "compute"},
    f"{APP_NAME}.generateInsights": {"queue": "compute"},
    f"{APP_NAME}.generateForecasts": {"queue": "compute"},
    f"{APP_NAME}.dailyBilling": {"queue": "billing"},
    f"{APP_NAME}.annualRenewal": {"queue": "billing"},
    f"{APP_NAME}.renewalLifecycle": {"queue": "billing"},
    f"{APP_NAME}.pastDueSuspension": {"queue": "billing"},
    f"{APP_NAME}.entitlementBoundary": {"queue": "billing"},
    f"{APP_NAME}.reconciliation": {"queue": "billing"},
    f"{APP_NAME}.billingMetrics": {"queue": "billing"},
    f"{APP_NAME}.subscriptionExpiry": {"queue": "billing"},
    f"{APP_NAME}.creditReconciliation": {"queue": "billing"},
    f"{APP_NAME}.syncSessionActivity": {"queue": "billing"},
}
