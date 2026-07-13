"""
billingMetricsService.py

Observability service for billing operations.

Provides metric collection for:
    - Recurring charge outcomes (queued, captured, failed).
    - Threshold redirect events.
    - Token precheck failures.
    - Reconciliation mismatches.

Also provides alert evaluation against configurable thresholds for:
    - Sudden failure-rate spikes.
    - Webhook processing backlogs.
    - Unresolved pending payment attempts.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["BillingMetricsService"]


from api.services.billing.billingEventService import BillingEventService
from api.commons import client
from utils.logger import logger
import datetime
import os


_FAILURE_RATE_SPIKE_THRESHOLD = float(
    os.environ.get("BILLING_FAILURE_RATE_THRESHOLD", "0.3")
)
_WEBHOOK_BACKLOG_THRESHOLD = int(
    os.environ.get("BILLING_WEBHOOK_BACKLOG_THRESHOLD", "50")
)
_UNRESOLVED_ATTEMPTS_THRESHOLD = int(
    os.environ.get("BILLING_UNRESOLVED_ATTEMPTS_THRESHOLD", "10")
)
_METRICS_WINDOW_HOURS = int(
    os.environ.get("BILLING_METRICS_WINDOW_HOURS", "24")
)




class BillingMetricsService:
    """
    Observability service for billing system health monitoring.

    Collects operational metrics from billing_events and WebhookEvents
    and evaluates them against configurable
    alert thresholds.
    """

    def __init__(self):
        self.client = client

    def collectMetrics(self) -> dict:
        """
        Collect a comprehensive snapshot of billing metrics for the
        configured time window.

        Returns:
            dict: Metric values keyed by category.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        windowStart = (
            now - datetime.timedelta(hours=_METRICS_WINDOW_HOURS)
        ).isoformat()

        metrics = {
            "collectedAt": now.isoformat(),
            "windowHours": _METRICS_WINDOW_HOURS,
            "recurring": self._collectRecurringMetrics(windowStart),
            "thresholdRedirects": self._collectThresholdRedirects(windowStart),
            "tokenPrecheckFailures": self._collectTokenPrecheckFailures(windowStart),
            "reconciliation": self._collectReconciliationMetrics(),
            "webhookBacklog": self._collectWebhookBacklog(),
        }

        logger.info(
            f"Billing metrics collected — "
            f"queued={metrics['recurring']['queued']}, "
            f"captured={metrics['recurring']['captured']}, "
            f"failed={metrics['recurring']['failed']}, "
            f"backlog={metrics['webhookBacklog']['count']}"
        )
        return metrics

    def evaluateAlerts(self) -> list[dict]:
        """
        Evaluate current metrics against configured alert thresholds.

        Returns:
            list[dict]: List of triggered alerts with severity and details.
        """
        metrics = self.collectMetrics()
        alerts = []

        failureRate = self._computeFailureRate(metrics["recurring"])
        if failureRate > _FAILURE_RATE_SPIKE_THRESHOLD:
            alerts.append({
                "alertType": "failure_rate_spike",
                "severity": "HIGH",
                "threshold": _FAILURE_RATE_SPIKE_THRESHOLD,
                "actualValue": round(failureRate, 4),
                "message": (
                    f"Payment failure rate {failureRate:.1%} exceeds "
                    f"threshold {_FAILURE_RATE_SPIKE_THRESHOLD:.1%}"
                ),
            })

        backlogCount = metrics["webhookBacklog"]["count"]
        if backlogCount > _WEBHOOK_BACKLOG_THRESHOLD:
            alerts.append({
                "alertType": "webhook_backlog",
                "severity": "MEDIUM",
                "threshold": _WEBHOOK_BACKLOG_THRESHOLD,
                "actualValue": backlogCount,
                "message": (
                    f"Webhook processing backlog ({backlogCount}) exceeds "
                    f"threshold ({_WEBHOOK_BACKLOG_THRESHOLD})"
                ),
            })

        unresolvedCount = metrics["reconciliation"]["unresolvedAttempts"]
        if unresolvedCount > _UNRESOLVED_ATTEMPTS_THRESHOLD:
            alerts.append({
                "alertType": "unresolved_attempts",
                "severity": "HIGH",
                "threshold": _UNRESOLVED_ATTEMPTS_THRESHOLD,
                "actualValue": unresolvedCount,
                "message": (
                    f"Unresolved payment attempts ({unresolvedCount}) exceeds "
                    f"threshold ({_UNRESOLVED_ATTEMPTS_THRESHOLD})"
                ),
            })

        if alerts:
            self._persistAlerts(alerts)

        return alerts

    def _collectRecurringMetrics(self, windowStart: str) -> dict:
        """
        Count recurring charge outcomes (queued, captured, failed) within
        the metrics window using billing_events.

        Args:
            windowStart: ISO timestamp for the start of the window.

        Returns:
            dict: Counts of queued, captured, and failed charges.
        """
        queued = self._countLogEvents(
            "billing.renewal_queued", windowStart
        )
        captured = self._countLogEvents(
            "billing.renewal_charged", windowStart
        ) + self._countLogEvents(
            "billing.annual_renewal_charged", windowStart
        )
        failed = self._countLogEvents(
            "billing.renewal_failed", windowStart
        ) + self._countLogEvents(
            "billing.annual_renewal_failed", windowStart
        )

        return {"queued": queued, "captured": captured, "failed": failed}

    def _collectThresholdRedirects(self, windowStart: str) -> dict:
        """
        Count threshold redirect events within the metrics window.

        Args:
            windowStart: ISO timestamp for the start of the window.

        Returns:
            dict: Count of threshold redirected charges.
        """
        count = self._countLogEvents(
            "billing.threshold_redirected", windowStart
        )
        return {"count": count}

    def _collectTokenPrecheckFailures(self, windowStart: str) -> dict:
        """
        Count token precheck failure events within the metrics window.

        Args:
            windowStart: ISO timestamp for the start of the window.

        Returns:
            dict: Count of precheck failures.
        """
        count = self._countLogEvents(
            "billing.token_precheck_failed", windowStart
        )
        return {"count": count}

    def _collectReconciliationMetrics(self) -> dict:
        """
        Count current unresolved billing event payment attempts and recent
        reconciliation anomaly reports.

        Returns:
            dict: Current unresolved count and anomaly counts.
        """
        unresolved = (
            self.client.table("billing_events")
            .select("id", count="exact")
            .eq("event_category", "payment_attempt")
            .in_("payment_status", ["created", "pending_provider_ack", "authorized"])
            .execute()
        )
        unresolvedCount = unresolved.count if hasattr(unresolved, "count") and unresolved.count is not None else len(unresolved.data)

        return {"unresolvedAttempts": unresolvedCount}

    def _collectWebhookBacklog(self) -> dict:
        """
        Count webhook events stuck in processing or failed states.

        Returns:
            dict: Current backlog count.
        """
        backlog = (
            self.client.table("WebhookEvents")
            .select("razorpayEventId", count="exact")
            .in_("status", ["processing", "failed"])
            .execute()
        )
        backlogCount = backlog.count if hasattr(backlog, "count") and backlog.count is not None else len(backlog.data)

        return {"count": backlogCount}

    def _countLogEvents(self, eventType: str, windowStart: str) -> int:
        """
        Count billing_events entries of a given eventType within the window.

        Args:
            eventType: The billing event type to count.
            windowStart: ISO timestamp for the start of the window.

        Returns:
            int: Number of matching log entries.
        """
        result = (
            self.client.table("billing_events")
            .select("id", count="exact")
            .eq("event_type", eventType)
            .gte("occurred_at", windowStart)
            .execute()
        )
        return result.count if hasattr(result, "count") and result.count is not None else len(result.data)

    @staticmethod
    def _computeFailureRate(recurringMetrics: dict) -> float:
        """
        Compute the failure rate from recurring charge metrics.

        Args:
            recurringMetrics: Dict with queued, captured, failed counts.

        Returns:
            float: Failure rate between 0.0 and 1.0.
        """
        total = recurringMetrics["captured"] + recurringMetrics["failed"]
        if total == 0:
            return 0.0
        return recurringMetrics["failed"] / total

    def _persistAlerts(self, alerts: list[dict]) -> None:
        """
        Log triggered alerts to billing_events for dashboard visibility.

        Args:
            alerts: List of triggered alert dicts.
        """
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for alert in alerts:
            try:
                BillingEventService(self.client).log_event(
                    user_id="system",
                    event_type=f"billing.alert.{alert['alertType']}",
                    event_status=alert["severity"],
                    category="system",
                    metadata={
                        **alert,
                        "triggeredAt": now,
                    },
                )
                logger.warning(
                    f"Billing alert triggered: {alert['alertType']} "
                    f"(severity={alert['severity']}, value={alert['actualValue']})"
                )
            except Exception as e:
                logger.error(f"Failed to persist billing alert: {e}")
