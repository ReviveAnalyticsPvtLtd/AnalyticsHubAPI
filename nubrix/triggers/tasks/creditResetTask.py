"""
creditResetTask.py

Periodic task for resetting monthly credit balances.

Runs daily at 01:30 UTC via Celery Beat. Queries all credit_balances
rows where the billing period has ended and resets their credits
for the new period.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["CreditResetTask"]


from utils.logger import logger


class CreditResetTask:
    """
    Periodic task that resets monthly credit balances for users
    whose billing period has ended.
    """

    def execute(self) -> dict:
        """
        Find all credit_balances rows where period_end <= NOW()
        and reset each user's credits for the new billing period.

        Returns:
            dict: Summary of the reset operation.
        """
        logger.info("Credit reset task started")

        try:
            from api.commons import client
            from api.services.credits.creditService import creditService
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            rows = (
                client.table("credit_balances")
                .select("user_id, period_end")
                .lte("period_end", now)
                .gt("monthly_quota", 0)
                .execute()
            )

            if not rows.data:
                logger.info("Credit reset task: no balances due for reset")
                return {"resetCount": 0}

            resetCount = 0
            for row in rows.data:
                userId = row.get("user_id")
                try:
                    creditService.resetMonthlyCredits(userId)
                    resetCount += 1
                except Exception as e:
                    logger.error(f"Credit reset failed for userId={userId}: {e}")

            logger.info(f"Credit reset task completed — resetCount={resetCount}")
            return {"resetCount": resetCount}
        except Exception as e:
            logger.error(f"Credit reset task failed: {e}")
            return {"error": str(e), "resetCount": 0}
