"""
subscriptionExpiryTask.py

Celery Beat task that runs the subscription expiry sweep daily.

Delegates to ``recalculateSubscriptionDays`` which transitions
trial, cancelled, and other stale subscriptions to ``expired``
once their ``current_period_end`` has passed. Also refreshes
each row's ``billing_state`` lifecycle snapshot.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["SubscriptionExpiryTask"]

from utils.logger import logger


class SubscriptionExpiryTask:
    """
    Daily sweep that expires subscriptions past their period end.
    """

    def execute(self) -> dict:
        """
        Run the subscription expiry recalculation.

        Returns:
            dict: Execution summary.
        """
        logger.info("Subscription expiry task started")
        from nubrix.components.subscriptionManager import recalculateSubscriptionDays
        recalculateSubscriptionDays()
        logger.info("Subscription expiry task completed")
        return {"status": "completed"}
