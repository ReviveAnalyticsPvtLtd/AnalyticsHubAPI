"""
billingMetricsTask.py

Periodic billing observability task.

Runs every 30 minutes via Celery Beat. Collects operational metrics
from the billing system and evaluates them against configured alert
thresholds. Triggered alerts are persisted to billing_events for
dashboard visibility.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["BillingMetricsTask"]


from utils.logger import logger


class BillingMetricsTask:
    """
    Periodic task for billing system health monitoring.

    Collects operational metrics and evaluates alert thresholds
    every run. Alerts are logged to billing_events for
    observability dashboards.
    """

    def execute(self) -> dict:
        """
        Collect billing metrics and evaluate alert thresholds.

        Returns:
            dict: Collected metrics and triggered alerts.
        """
        logger.info("Billing metrics task started")

        try:
            from api.services.billing.billingMetricsService import BillingMetricsService
            service = BillingMetricsService()
            metrics = service.collectMetrics()
            alerts = service.evaluateAlerts()

            logger.info(
                f"Billing metrics task completed — "
                f"alerts_triggered={len(alerts)}"
            )
            return {
                "metrics": metrics,
                "alertsTriggered": len(alerts),
                "alerts": alerts,
            }
        except Exception as e:
            logger.error(f"Billing metrics task failed: {e}")
            return {"error": str(e), "alertsTriggered": 0}
