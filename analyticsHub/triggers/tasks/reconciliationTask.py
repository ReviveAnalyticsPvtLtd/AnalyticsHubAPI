"""
reconciliationTask.py

Periodic reconciliation scheduler for payment_attempts that remain
in unresolved states (created, pending_provider_ack).

Runs every 15 minutes via Celery Beat. For each stale attempt,
queries Razorpay for the authoritative payment/order status and
finalizes the attempt row idempotently (captured, failed, expired).

This closes Gap 3: ensures no unresolved payment attempt is left
indefinitely without a reconciliation path, even if webhooks are
delayed or missed entirely.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["ReconciliationTask"]

from supabase import create_client
from utils.logger import logger
import razorpay
import datetime
import os


_STALE_THRESHOLD_MINUTES = 30
_BATCH_LIMIT = 50


def _getSupabaseClient():
    """
    Create and return a Supabase client using environment variables.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


class ReconciliationTask:
    """
    Reconciliation scheduler that finalizes stale payment_attempts by
    querying the Razorpay API for authoritative payment/order status.
    """

    def __init__(self):
        self.client = _getSupabaseClient()
        self.razorpayClient = razorpay.Client(
            auth=(
                os.environ.get("RAZORPAY_KEY_ID", ""),
                os.environ.get("RAZORPAY_KEY_SECRET", ""),
            )
        )

    def execute(self) -> dict:
        """
        Run a reconciliation sweep.

        Returns:
            dict: Counts of resolved and errored attempts.
        """
        logger.info("Reconciliation task started")
        results = self._reconcileStaleAttempts()
        logger.info(
            f"Reconciliation task completed: {results['resolved']} resolved, "
            f"{results['errors']} errors"
        )
        return results

    def _reconcileStaleAttempts(self) -> dict:
        """
        Fetch stale pending attempts older than the threshold and resolve
        each by querying the Razorpay API.

        Returns:
            dict: Counts of resolved and errored attempts.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = (now - datetime.timedelta(minutes=_STALE_THRESHOLD_MINUTES)).isoformat()

        staleAttempts = (
            self.client.table("payment_attempts")
            .select("id, provider_payment_id, provider_order_id, user_id, status")
            .in_("status", ["created", "pending_provider_ack"])
            .lte("attempted_at", cutoff)
            .limit(_BATCH_LIMIT)
            .execute()
            .data
        )

        if not staleAttempts:
            logger.info("No stale payment attempts to reconcile")
            return {"resolved": 0, "errors": 0}

        resolved = 0
        errors = 0

        for attempt in staleAttempts:
            attemptId = attempt["id"]
            try:
                finalStatus = self._resolveAttempt(attempt)
                if finalStatus:
                    updateData = {
                        "status": finalStatus,
                        "completed_at": now.isoformat(),
                    }
                    self.client.table("payment_attempts").update(
                        updateData
                    ).eq("id", attemptId).execute()
                    resolved += 1
                    logger.info(
                        f"Reconciled attempt {attemptId} for user "
                        f"{attempt['user_id']} -> {finalStatus}"
                    )
            except Exception as e:
                logger.error(f"Reconciliation failed for attempt {attemptId}: {e}")
                errors += 1

        return {"resolved": resolved, "errors": errors}

    def _resolveAttempt(self, attempt: dict) -> str | None:
        """
        Query Razorpay to determine the terminal status of a payment attempt.

        Checks by provider_payment_id first, falls back to provider_order_id.

        Args:
            attempt (dict): The payment_attempts row.

        Returns:
            str | None: The resolved status (captured/failed/expired) or None
            if status cannot be determined yet.
        """
        paymentId = attempt.get("provider_payment_id")
        if paymentId:
            return self._resolveByPaymentId(paymentId)

        orderId = attempt.get("provider_order_id")
        if orderId:
            return self._resolveByOrderId(orderId)

        if attempt.get("status") == "created":
            return "failed"

        return None

    def _resolveByPaymentId(self, paymentId: str) -> str | None:
        """
        Fetch payment status from Razorpay by payment ID.

        Args:
            paymentId (str): Razorpay payment ID.

        Returns:
            str | None: Mapped terminal status or None if still pending.
        """
        try:
            payment = self.razorpayClient.payment.fetch(paymentId)
            return self._mapPaymentStatus(payment.get("status", ""))
        except Exception as e:
            logger.warning(f"Failed to fetch payment {paymentId} for reconciliation: {e}")
            return None

    def _resolveByOrderId(self, orderId: str) -> str | None:
        """
        Fetch order from Razorpay and check its payment status.

        Args:
            orderId (str): Razorpay order ID.

        Returns:
            str | None: Mapped terminal status or None if still pending.
        """
        try:
            order = self.razorpayClient.order.fetch(orderId)
            orderStatus = order.get("status", "")
            if orderStatus == "paid":
                return "captured"
            if orderStatus in ("expired", "cancelled"):
                return orderStatus
            payments = self.razorpayClient.order.payments(orderId)
            if not payments or not payments.get("items"):
                return None
            for payment in payments["items"]:
                mapped = self._mapPaymentStatus(payment.get("status", ""))
                if mapped:
                    return mapped
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch order {orderId} for reconciliation: {e}")
            return None

    @staticmethod
    def _mapPaymentStatus(razorpayStatus: str) -> str | None:
        """
        Map a Razorpay payment status to a payment_attempts terminal status.

        Args:
            razorpayStatus (str): The Razorpay payment status string.

        Returns:
            str | None: Mapped status or None if the payment is not terminal.
        """
        statusMap = {
            "captured": "captured",
            "failed": "failed",
            "expired": "expired",
            "refunded": "captured",
        }
        return statusMap.get(razorpayStatus)
