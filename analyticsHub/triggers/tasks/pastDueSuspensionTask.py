"""
pastDueSuspensionTask.py

High-frequency Celery Beat scheduler for past-due and suspension
lifecycle transitions on annual prepaid subscriptions.

Runs every 30 minutes to ensure near-real-time detection of:

    Past-Due:
        Subscriptions whose billing period has ended with an unpaid
        renewal invoice are transitioned to past_due immediately.

    Suspension:
        Subscriptions in past_due state where the reconciliation buffer
        window (60 minutes) has elapsed are suspended.

Both transitions re-read invoice status before mutation to guard
against race conditions with concurrent webhook-paid events.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["PastDueSuspensionTask"]


from api.services.invoiceService import transitionToPastDue, transitionToSuspended
from supabase import create_client
from utils.logger import logger
import datetime
import os


_RECONCILIATION_BUFFER_MINUTES = 60


def _getSupabaseClient():
    """
    Create and return a Supabase client.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


class PastDueSuspensionTask:
    """
    High-frequency scheduler for past-due and suspension lifecycle
    transitions on annual prepaid renewal invoices.
    """

    def __init__(self):
        self.client = _getSupabaseClient()

    def execute(self) -> dict:
        """
        Run past-due and suspension sweeps.

        Returns:
            dict: Counts for past_due and suspension operations.
        """
        logger.info("Past-due/suspension task started")
        pastDueResults = self._sweepPastDue()
        suspensionResults = self._sweepSuspension()
        logger.info(
            f"Past-due/suspension task completed — "
            f"past_due: {pastDueResults['transitioned']}, "
            f"suspensions: {suspensionResults['suspended']}"
        )
        return {
            "past_due": pastDueResults,
            "suspension": suspensionResults,
        }

    def _sweepPastDue(self) -> dict:
        """
        Find subscriptions where the billing period has ended and the
        renewal invoice is still unpaid, then transition to past_due.

        Returns:
            dict: Counts of transitioned, skipped, and errored operations.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        transitioned = 0
        skipped = 0
        errors = 0

        subscriptions = (
            self.client.table("subscriptions")
            .select("id, user_id, current_period_end, status, billing_mode")
            .eq("billing_mode", "annual_prepaid")
            .in_("status", ["active", "renewal_upcoming", "payment_pending"])
            .lte("current_period_end", now.isoformat())
            .execute()
            .data
        )

        for subscription in subscriptions:
            subscriptionId = subscription["id"]
            try:
                invoices = (
                    self.client.table("Invoices")
                    .select("id, status")
                    .eq("subscription_id", subscriptionId)
                    .eq("billing_reason", "renewal")
                    .in_("status", ["upcoming", "payment_pending"])
                    .limit(1)
                    .execute()
                    .data
                )
                if not invoices:
                    skipped += 1
                    continue

                if transitionToPastDue(subscription, invoices[0]):
                    transitioned += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(
                    f"Past-due transition failed for subscription {subscriptionId}: {e}"
                )
                errors += 1

        return {"transitioned": transitioned, "skipped": skipped, "errors": errors}

    def _sweepSuspension(self) -> dict:
        """
        Find past_due subscriptions where the reconciliation buffer has
        elapsed, then transition to suspended.

        Returns:
            dict: Counts of suspended, skipped, and errored operations.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        bufferCutoff = (
            now - datetime.timedelta(minutes=_RECONCILIATION_BUFFER_MINUTES)
        ).isoformat()
        suspended = 0
        skipped = 0
        errors = 0

        subscriptions = (
            self.client.table("subscriptions")
            .select("id, user_id, current_period_end, status, billing_mode")
            .eq("billing_mode", "annual_prepaid")
            .eq("status", "past_due")
            .lte("current_period_end", bufferCutoff)
            .execute()
            .data
        )

        for subscription in subscriptions:
            subscriptionId = subscription["id"]
            try:
                invoices = (
                    self.client.table("Invoices")
                    .select("id, status")
                    .eq("subscription_id", subscriptionId)
                    .eq("billing_reason", "renewal")
                    .in_("status", ["upcoming", "payment_pending"])
                    .limit(1)
                    .execute()
                    .data
                )
                if not invoices:
                    skipped += 1
                    continue

                if transitionToSuspended(subscription, invoices[0]):
                    suspended += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(
                    f"Suspension transition failed for subscription {subscriptionId}: {e}"
                )
                errors += 1

        return {"suspended": suspended, "skipped": skipped, "errors": errors}
