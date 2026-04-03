"""
analyticsHub/components/subscriptionManager.py

This module provides utility functions for managing user subscription expiry calculations,
Razorpay subscription status synchronization, and sending warning emails when subscriptions
are about to expire.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["recalculateSubscriptionDays", "syncSubscriptionStatuses"]


from supabase import create_client
from datetime import datetime, timezone
from dateutil import parser
from loguru import logger
import razorpay
import requests
import time
import os


RAZORPAY_STATUS_MAP = {
    "created": "pending",
    "authenticated": "pending",
    "active": "active",
    "halted": "expired",
    "cancelled": "cancelled",
    "paused": "paused",
    "completed": "expired",
    "expired": "expired",
    "pending": "pending",
}


def _getSupabaseClient():
    """
    Create and return a Supabase client using environment variables.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def recalculateSubscriptionDays() -> None:
    """
    Recalculates subscription days left for all users with a subscription expiry date.
    Updates the subscriptionDaysLeft field in the Users table. Expired subscriptions
    are immediately set to expired status (no grace period). Sends warning emails
    when exactly 2 days are remaining.

    Returns:
        None

    Raises:
        Exception: For any errors during the recalculation process.
    """
    edgeFunctionUrl = os.environ["FREE_TRIAL_EXPIRY_WARNING_EMAIL_URL"]
    client = _getSupabaseClient()
    now = datetime.now(timezone.utc)
    users = client.table("Users") \
        .select("userId, email, fullName, subscriptionStart, subscriptionExpiry, subscriptionDaysLeft, subscriptionStatus") \
        .not_.is_("subscriptionExpiry", "null") \
        .execute().data
    for user in users:
        expiryRaw = user["subscriptionExpiry"]
        expiry = parser.parse(expiryRaw)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        else:
            expiry = expiry.astimezone(timezone.utc)
        deltaDays = (expiry.date() - now.date()).days
        if deltaDays < 0:
            newDaysLeft = -1
            if user.get("subscriptionStatus") != "expired":
                client.table("Users").update({
                    "subscriptionStatus": "expired"
                }).eq("userId", user["userId"]).execute()
        else:
            newDaysLeft = deltaDays
        client.table("Users") \
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
    logger.info("Subscription days recalculation completed (UTC)")
    return


def syncSubscriptionStatuses() -> None:
    """
    Synchronize local subscription statuses with Razorpay as the source of truth.

    Fetches all users with a Razorpay subscription ID and compares their local
    subscriptionStatus against the actual status from Razorpay API. Updates
    mismatches and logs discrepancies to the PaymentAuditLog table.

    Returns:
        None
    """
    client = _getSupabaseClient()
    razorpayKeyId = os.environ.get("RAZORPAY_KEY_ID")
    razorpayKeySecret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not razorpayKeyId or not razorpayKeySecret:
        logger.warning("Razorpay credentials not configured, skipping subscription status sync.")
        return
    razorpayClient = razorpay.Client(auth=(razorpayKeyId, razorpayKeySecret))
    users = client.table("Users") \
        .select("userId, razorpaySubscriptionId, subscriptionStatus") \
        .not_.is_("razorpaySubscriptionId", "null") \
        .execute().data
    syncCount = 0
    for user in users:
        subscriptionId = user["razorpaySubscriptionId"]
        localStatus = user.get("subscriptionStatus", "none")
        try:
            subscription = razorpayClient.subscription.fetch(subscriptionId)
            razorpayStatus = subscription.get("status", "")
            mappedStatus = RAZORPAY_STATUS_MAP.get(razorpayStatus, razorpayStatus)
            if mappedStatus and mappedStatus != localStatus:
                client.table("Users").update({
                    "subscriptionStatus": mappedStatus
                }).eq("userId", user["userId"]).execute()
                client.table("PaymentAuditLog").insert({
                    "userId": user["userId"],
                    "action": "manual_sync",
                    "razorpaySubscriptionId": subscriptionId,
                    "status": mappedStatus,
                    "metadata": {
                        "previousStatus": localStatus,
                        "razorpayStatus": razorpayStatus
                    }
                }).execute()
                syncCount += 1
                logger.info(f"Synced user {user['userId']}: {localStatus} -> {mappedStatus}")
        except Exception as e:
            logger.error(f"Failed to sync subscription {subscriptionId} for user {user['userId']}: {e}")
        time.sleep(0.2)
    logger.info(f"Subscription status sync completed. {syncCount} users updated.")
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
    syncSubscriptionStatuses()
