"""
creditReconciliationTask.py

Periodic task for reconciling Redis credit balances with Supabase.

Runs every hour via Celery Beat. Ensures that the Redis real-time
balance and the durable Supabase credit_balances row stay in sync.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["CreditReconciliationTask"]


from utils.logger import logger


class CreditReconciliationTask:
    """
    Periodic task that syncs Redis credit balances back to the
    Supabase credit_balances table.
    """

    def execute(self) -> dict:
        """
        Query all credit_balances rows and reconcile each user's
        Redis balance with the database.

        Returns:
            dict: Summary of the reconciliation operation.
        """
        logger.info("Credit reconciliation task started")

        try:
            from api.commons import client
            from api.services.credits.creditService import creditService

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

            logger.info(
                f"Credit reconciliation task completed — "
                f"reconciledCount={reconciledCount}"
            )
            return {"reconciledCount": reconciledCount}
        except Exception as e:
            logger.error(f"Credit reconciliation task failed: {e}")
            return {"error": str(e), "reconciledCount": 0}
