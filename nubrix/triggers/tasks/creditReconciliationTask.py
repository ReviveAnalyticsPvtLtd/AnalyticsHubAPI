"""
creditReconciliationTask.py

Periodic task for reconciling Redis credit balances with Supabase.

Runs every hour via Celery Beat. Ensures that the Redis real-time
balance and the durable Supabase credit_balances row stay in sync, and
completes any credit top-up whose payment succeeded but whose grant never ran.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["CreditReconciliationTask", "sweepStuckTopupPurchases"]


from utils.logger import logger


def sweepStuckTopupPurchases(olderThanMinutes: int = 15) -> dict:
    """
    Complete top-up grants whose payment succeeded but whose grant never ran.

    Covers the case where the payment.captured webhook never arrived and the
    user closed the tab before /credits/topup/verify fired. grant_topup_tokens
    is idempotent, so a repeated sweep cannot double-credit.

    Args:
        olderThanMinutes (int): Only consider invoices older than this, so
            orders still mid-checkout are left alone.

    Returns:
        dict: Summary with checked and granted counts.
    """
    from api.services.subscriptions.subscriptionService import subscriptionService
    from api.services.credits.creditService import creditService
    from datetime import datetime, timedelta, timezone
    from api.commons import client

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=olderThanMinutes)).isoformat()
    checked = 0
    granted = 0

    try:
        rows = (
            client.table("Invoices")
            .select("id, userId, razorpay_order_id")
            .eq("billing_reason", "add_on")
            .in_("status", ["UPCOMING", "PAYMENT_PENDING", "upcoming", "payment_pending"])
            .lt("created_at", cutoff)
            .execute()
        ).data or []
    except Exception as e:
        logger.warning(f"Stuck top-up sweep could not list invoices: {e}")
        return {"checked": 0, "granted": 0}

    for row in rows:
        orderId = row.get("razorpay_order_id")
        userId = row.get("userId")
        if not orderId or not userId:
            continue
        checked += 1
        try:
            order = subscriptionService.razorpayClient.order.fetch(orderId)
            if order.get("status") != "paid":
                continue
            payments = subscriptionService.razorpayClient.order.payments(orderId)
            captured = next(
                (payment for payment in (payments.get("items") or [])
                 if payment.get("status") in ("captured", "authorized")),
                None,
            )
            if not captured:
                continue
            result = creditService.grantTopupTokens(userId, orderId, captured["id"])
            if result["granted"]:
                granted += 1
                logger.info(
                    f"Stuck top-up recovered — userId={userId}, order={orderId}, "
                    f"tokens={result['tokens']}"
                )
        except Exception as e:
            logger.warning(f"Stuck top-up sweep failed for order {orderId}: {e}")

    if checked:
        logger.info(
            f"Stuck top-up sweep complete — checked={checked}, granted={granted}"
        )
    return {"checked": checked, "granted": granted}


class CreditReconciliationTask:
    """
    Periodic task that syncs Redis credit balances back to the
    Supabase credit_balances table.
    """

    def __init__(self, client=None, creditService=None):
        self._client = client
        self._creditService = creditService

    def execute(self) -> dict:
        """
        Query all credit_balances rows and reconcile each user's
        Redis balance with the database.

        Returns:
            dict: Summary of the reconciliation operation.
        """
        logger.info("Credit reconciliation task started")

        try:
            if self._client is None:
                from api.commons import client
            else:
                client = self._client
            if self._creditService is None:
                from api.services.credits.creditService import creditService
            else:
                creditService = self._creditService

            rows = (
                client.table("credit_balances")
                .select("user_id")
                .gt("monthly_token_quota", 0)
                .execute()
            )

            if not rows.data:
                logger.info("Credit reconciliation task: no balances to reconcile")
                return {"reconciledCount": 0}

            reconciledCount = 0
            for row in rows.data:
                userId = row.get("user_id")
                try:
                    creditService.reconcile(userId)
                    reconciledCount += 1
                except Exception as e:
                    logger.warning(f"Credit reconciliation failed for userId={userId}: {e}")

            sweepResult = sweepStuckTopupPurchases()

            logger.info(
                f"Credit reconciliation task completed — "
                f"reconciledCount={reconciledCount}, "
                f"topupChecked={sweepResult['checked']}, "
                f"topupGranted={sweepResult['granted']}"
            )
            return {
                "reconciledCount": reconciledCount,
                "topupSweep": sweepResult,
            }
        except Exception as e:
            logger.error(f"Credit reconciliation task failed: {e}")
            return {"error": str(e), "reconciledCount": 0}
