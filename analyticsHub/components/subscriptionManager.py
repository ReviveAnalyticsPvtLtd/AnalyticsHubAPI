"""
analyticsHub/components/subscriptionManager.py

This module provides utility functions for managing user subscription expiry
calculations and sending warning emails when subscriptions are about to expire.

Reads all lifecycle data from the canonical ``subscriptions`` table.
If a subscription row is missing for a user, logs a data-integrity
error and skips mutation (no fallback to legacy Users columns).
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["recalculateSubscriptionDays"]


from supabase import create_client
from datetime import datetime, timezone
from utils.logger import logger
from api.services.paymentValidationService import parseUtc
import requests
import os


def _getSupabaseClient():
    """
    Create and return a Supabase client using environment variables.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def _auditSubscriptionIntegrityIssue(client, userId: str, reason: str, metadata: dict | None = None) -> None:
    """
    Record subscription lifecycle integrity issues in SubscriptionLog.

    Args:
        client: Supabase client.
        userId (str): Internal user ID.
        reason (str): Machine-readable reason for the integrity failure.
        metadata (dict | None): Optional additional context.
    """
    try:
        payload = {
            "userId": userId,
            "eventType": "billing.subscription_row_missing",
            "status": "INTEGRITY_ERROR",
            "metadata": {"reason": reason, **(metadata or {})},
        }
        client.table("SubscriptionLog").insert(payload).execute()
    except Exception as e:
        logger.error(f"Failed to write subscription integrity audit log for user {userId}: {e}")


def recalculateSubscriptionDays() -> None:
    """
    Recalculates subscription lifecycle status from canonical subscriptions rows.

    Expired subscriptions are immediately set to expired status (no grace period).
    Sends warning emails when exactly 2 days are remaining until current_period_end.

    Annual prepaid subscriptions are managed by dedicated renewal schedulers,
    except cancelled rows, which are expired here once their paid period ends.

    Returns:
        None

    Raises:
        Exception: For any errors during the recalculation process.
    """
    edgeFunctionUrl = os.environ["FREE_TRIAL_EXPIRY_WARNING_EMAIL_URL"]
    client = _getSupabaseClient()
    now = datetime.now(timezone.utc)
    subscriptions = client.table("subscriptions") \
        .select("id, user_id, current_period_start, current_period_end, status, billing_mode") \
        .not_.is_("current_period_end", "null") \
        .execute().data

    subscriptionUserIds = {s.get("user_id") for s in subscriptions if s.get("user_id")}
    try:
        users = client.table("Users") \
            .select("userId") \
            .execute().data
        userIds = {user.get("userId") for user in users if user.get("userId")}
        for userId in userIds:
            if userId not in subscriptionUserIds:
                logger.error(
                    f"Data integrity issue: missing subscriptions row for user {userId}"
                )
                _auditSubscriptionIntegrityIssue(
                    client=client,
                    userId=userId,
                    reason="missing_canonical_subscription_row",
                    metadata={},
                )
    except Exception as e:
        logger.error(f"Failed subscription integrity precheck in recalculateSubscriptionDays: {e}")

    for subscription in subscriptions:
        billingMode = subscription.get("billing_mode", "monthly_recurring")
        currentStatus = (subscription.get("status") or "").lower()
        if billingMode == "annual_prepaid" and currentStatus != "cancelled":
            continue

        expiryRaw = subscription["current_period_end"]
        expiry = parseUtc(expiryRaw)
        if not expiry:
            continue

        deltaDays = (expiry.date() - now.date()).days

        if deltaDays < 0:
            if currentStatus not in ("expired", "suspended"):
                client.table("subscriptions").update({
                    "status": "expired"
                }).eq("id", subscription["id"]).execute()

        if deltaDays == 2:
            userData = client.table("Users") \
                .select("email, fullName") \
                .eq("userId", subscription["user_id"]) \
                .limit(1) \
                .execute().data
            if not userData:
                logger.warning(
                    f"Skipping warning email: user not found for subscription "
                    f"{subscription['id']}"
                )
                continue
            user = userData[0]
            _sendSubscriptionWarningMail(
                edgeFunctionUrl = edgeFunctionUrl,
                email = user["email"],
                fullName = user["fullName"],
                subscriptionStart = subscription.get("current_period_start")
            )

    logger.info("Subscription days recalculation completed (UTC)")
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
