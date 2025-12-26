"""
subscriptionService.py

This module provides the SubscriptionService class, which encapsulates all business logic related to user subscriptions, including managing subscription plans, billing, and payment processing. It interacts with the Supabase client and manages subscription records in the database.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["subscriptionService"]      


from utils.exceptionHandler import CustomException
from utils.logger import logger
from api.commons import client
import datetime

class SubscriptionService:    
    """
    Service class for user subscription management.

    Handles subscription plans, billing, payment processing, and related operations.
    Interacts with the Supabase client and manages subscription records in the database.
    """
    def __init__(self) -> None:
        """
        Initialize the SubscriptionService and set up the Supabase client.
        """
        logger.info("Initializing Subscription Service.")
        self.client = client

    def activateFreeTrial(self, userId: str) -> None:
        """
        Activate a free trial for a user.

        Args:
            userId (str): The ID of the user to activate the free trial for.
        """
        try:
            currentTime = datetime.datetime.utcnow()
            trialDurationDays = 12
            subscriptionStart = currentTime
            subscriptionExpiry = currentTime + datetime.timedelta(days=trialDurationDays)
            updateData = {
                "subscriptionPlan": "free",
                "subscriptionStart": str(subscriptionStart),
                "subscriptionExpiry": str(subscriptionExpiry)
            }
            _ = self.client.table("Users").update(updateData).eq("userId", userId).execute()
            return
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception   

subscriptionService = SubscriptionService()  