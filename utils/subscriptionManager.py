"""
subscriptionManager.py

This module provides utility functions for managing user subscription expiry calculations
and sending warning emails when subscriptions are about to expire.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["recalculateSubscriptionDays"]


from supabase import create_client
from datetime import datetime
from dateutil import parser
from loguru import logger
import requests
import os


def recalculateSubscriptionDays() -> None:
    """
    Recalculates subscription days left for all users with a subscription expiry date.
    Updates the subscriptionDaysLeft field in the Users table and sends warning emails
    when exactly 2 days are remaining.

    Returns:
        None

    Raises:
        Exception: For any errors during the recalculation process.
    """
    supabaseUrl = os.environ["SUPABASE_URL"]
    supabaseKey = os.environ["SUPABASE_KEY"]
    edgeFunctionUrl = os.environ["EDGE_FUNCTION_URL"]
    client = create_client(supabaseUrl, supabaseKey)
    now = datetime.now()
    users = client.table("Users") \
        .select("userId, email, fullName, subscriptionStart, subscriptionExpiry, subscriptionDaysLeft") \
        .not_.is_("subscriptionExpiry", "null") \
        .execute().data
    for user in users:
        expiryRaw = user["subscriptionExpiry"]
        expiry = parser.parse(expiryRaw)
        deltaDays = (expiry.date() - now.date()).days
        if deltaDays < 0:
            newDaysLeft = -1
        else:
            newDaysLeft = deltaDays
        _ = client.table("Users") \
            .update({"subscriptionDaysLeft": newDaysLeft}) \
            .eq("userId", user["userId"]) \
            .execute()
        if newDaysLeft == 2:
            _sendSubscriptionWarningMail(
                edgeFunctionUrl = edgeFunctionUrl,
                email = user["email"],
                fullName = user["fullName"],
                subscriptionStart = user["subscriptionStart"]
            )
    logger.info("Subscription days recalculation completed (local time)")
    return


def _sendSubscriptionWarningMail(edgeFunctionUrl: str, email: str, fullName: str, subscriptionStart: str) -> None:
    """
    Sends a warning email to a user when their subscription is about to expire.

    Args:
        edgeFunctionUrl (str): The URL of the edge function to invoke for sending mail.
        email (str): The user's email address.
        fullName (str): The user's full name.
        subscriptionStart (str): The subscription start date.

    Returns:
        None
    """
    try:
        response = requests.post(
            edgeFunctionUrl,
            json = {
                "email": email,
                "name": fullName,
                "trialStartDate": subscriptionStart
            },
            timeout = 10
        )
        if response.status_code >= 300:
            logger.warning(f"Mail failed for {email}: {response.text}")
        else:
            logger.info(f"Warning mail sent to {email}")
    except Exception as e:
        logger.error(f"Exception sending mail to {email}: {e}")
    return


if __name__ == "__main__":
    recalculateSubscriptionDays()
