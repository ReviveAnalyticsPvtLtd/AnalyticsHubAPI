"""
annualRenewalTask.py

Celery Beat scheduler for the annual prepaid renewal invoice lifecycle.

Runs daily and handles two distinct timeline windows:

    T-30 sweep:
        Finds annual subscriptions whose current_period_end is within
        30 days and creates an estimated upcoming renewal invoice via
        invoiceService.createUpcomingRenewalInvoice. Sends a T-30
        awareness email on first creation.

    T-7 sweep:
        Finds upcoming renewal invoices whose due_date is within 7 days,
        recomputes final pricing, freezes the snapshot, and creates a
        Razorpay Invoice payment artifact via
        invoiceService.createPaymentArtifact. Sends a T-7 payment
        ready email with the pay link.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["AnnualRenewalTask"]


from api.services.invoiceService import (
    createUpcomingRenewalInvoice,
    createPaymentArtifact,
)
from supabase import create_client
from utils.logger import logger
import datetime
import requests
import json
import os

_EMAIL_LOCK_TTL_SECONDS = 120


def _getSupabaseClient():
    """
    Create and return a Supabase client.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


class AnnualRenewalTask:
    """
    Daily scheduler for annual prepaid renewal invoice lifecycle.

    Executes two sweeps per run:
        1. T-30: Create upcoming renewal invoices + send awareness email.
        2. T-7: Create payment artifacts + send pay link email.
    """

    def __init__(self):
        self.client = _getSupabaseClient()
        import redis
        self.redisClient = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            password=os.environ.get("REDIS_PASSWORD", None),
            decode_responses=True,
        )

    def execute(self) -> dict:
        """
        Run the full annual renewal sweep.

        Returns:
            dict: Counts for t30 and t7 operations.
        """
        logger.info("Annual renewal task started")
        t30Results = self._sweepT30()
        t7Results = self._sweepT7()
        logger.info(
            f"Annual renewal task completed — "
            f"T30: {t30Results['created']} created, {t30Results['skipped']} skipped, "
            f"{t30Results['errors']} errors | "
            f"T7: {t7Results['created']} created, {t7Results['skipped']} skipped, "
            f"{t7Results['errors']} errors"
        )
        return {"t30": t30Results, "t7": t7Results}

    def _sweepT30(self) -> dict:
        """
        Find annual subscriptions due within 30 days and create
        upcoming renewal invoices. Sends T-30 awareness email
        idempotently on first creation.

        Returns:
            dict: Counts of created, skipped, and errored invoices.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        windowEnd = (now + datetime.timedelta(days=30)).isoformat()

        subscriptions = (
            self.client.table("subscriptions")
            .select("id, user_id, current_period_start, current_period_end, status, billing_mode")
            .eq("billing_mode", "annual_prepaid")
            .in_("status", ["active", "renewal_upcoming"])
            .gte("current_period_end", now.isoformat())
            .lte("current_period_end", windowEnd)
            .execute()
            .data
        )

        if not subscriptions:
            logger.info("T-30: No annual subscriptions approaching renewal")
            return {"created": 0, "skipped": 0, "errors": 0}

        created = 0
        skipped = 0
        errors = 0

        for subscription in subscriptions:
            userId = subscription["user_id"]
            try:
                userRows = (
                    self.client.table("Users")
                    .select("userId, email, fullName, domainCount, billingState, razorpayCustomerId")
                    .eq("userId", userId)
                    .limit(1)
                    .execute()
                    .data
                )
                if not userRows:
                    logger.warning(f"T-30: User not found for subscription {subscription['id']}")
                    skipped += 1
                    continue

                result = createUpcomingRenewalInvoice(subscription, userRows[0])
                if result:
                    self._transitionToRenewalUpcoming(subscription)
                    self._sendT30Email(userRows[0], result, subscription)
                    created += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(
                    f"T-30: Error processing subscription {subscription['id']}: {e}"
                )
                errors += 1

        return {"created": created, "skipped": skipped, "errors": errors}

    def _sweepT7(self) -> dict:
        """
        Find upcoming renewal invoices due within 7 days and create
        Razorpay payment artifacts. Sends T-7 payment ready email
        with the pay link on successful artifact creation.

        Returns:
            dict: Counts of created, skipped, and errored artifacts.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        windowEnd = (now + datetime.timedelta(days=7)).isoformat()

        invoices = (
            self.client.table("Invoices")
            .select("id, subscription_id, userId, status, due_date, period_start, period_end, "
                    "razorpayInvoiceId, razorpay_payment_link_id, total_amount, currency")
            .in_("status", ["upcoming", "payment_pending"])
            .eq("billing_reason", "renewal")
            .not_.is_("due_date", "null")
            .gte("due_date", now.isoformat())
            .lte("due_date", windowEnd)
            .execute()
            .data
        )

        if not invoices:
            logger.info("T-7: No renewal invoices approaching due date")
            return {"created": 0, "skipped": 0, "errors": 0}

        created = 0
        skipped = 0
        errors = 0

        for invoice in invoices:
            if invoice.get("razorpayInvoiceId") or invoice.get("razorpay_payment_link_id"):
                skipped += 1
                continue

            userId = invoice.get("userId", "")
            try:
                userRows = (
                    self.client.table("Users")
                    .select(
                        "userId, email, fullName, domainCount, phoneNumber, "
                        "billingState, razorpayCustomerId"
                    )
                    .eq("userId", userId)
                    .limit(1)
                    .execute()
                    .data
                )
                if not userRows:
                    logger.warning(f"T-7: User not found for invoice {invoice['id']}")
                    skipped += 1
                    continue

                result = createPaymentArtifact(invoice, userRows[0])
                if result:
                    self._sendT7Email(userRows[0], result)
                    created += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"T-7: Error creating artifact for invoice {invoice['id']}: {e}")
                errors += 1

        return {"created": created, "skipped": skipped, "errors": errors}

    def _transitionToRenewalUpcoming(self, subscription: dict) -> None:
        """
        Transition a subscription to renewal_upcoming if it is currently active.

        Args:
            subscription: The subscription row.
        """
        if subscription.get("status") == "active":
            self.client.table("subscriptions").update({
                "status": "renewal_upcoming",
            }).eq("id", subscription["id"]).execute()

    def _sendT30Email(self, user: dict, invoice: dict, subscription: dict) -> None:
        """
        Send a T-30 renewal awareness email.

        Uses SubscriptionLog as an idempotent send-log keyed by
        invoiceId + template to ensure the email is sent exactly once.

        Args:
            user: User row (email, fullName, domainCount).
            invoice: The created invoice row (id, total_amount).
            subscription: The subscription row (current_period_end).
        """
        template = "renewal_notice_t30"
        invoiceId = invoice.get("id", "")
        userId = user.get("userId", "")
        sendLogKey = f"{invoiceId}:{template}"
        lockKey = f"renewal-email:{sendLogKey}"
        lockAcquired = self.redisClient.set(
            lockKey, "1", nx=True, ex=_EMAIL_LOCK_TTL_SECONDS
        )
        if not lockAcquired:
            return

        existingLog = (
            self.client.table("SubscriptionLog")
            .select("id")
            .eq("userId", userId)
            .eq("eventType", f"email.{template}")
            .eq("status", sendLogKey)
            .limit(1)
            .execute()
            .data
        )
        if existingLog:
            return

        emailUrl = os.environ.get("RENEWAL_REMINDER_EMAIL_URL")
        if not emailUrl:
            logger.info("T-30 email skipped: RENEWAL_REMINDER_EMAIL_URL not configured")
            return

        payload = {
            "email": user.get("email", ""),
            "name": user.get("fullName", ""),
            "template": template,
            "amount": invoice.get("total_amount", 0),
            "currency": "INR",
            "domainCount": user.get("domainCount", 0),
            "renewalDate": subscription.get("current_period_end", ""),
            "estimateNote": True,
        }

        try:
            requests.post(
                url=emailUrl,
                data=json.dumps(payload),
                headers={"Authorization": f"Bearer {os.environ.get('SUPABASE_KEY', '')}"},
                timeout=10,
            )
            self.client.table("SubscriptionLog").insert({
                "userId": userId,
                "eventType": f"email.{template}",
                "status": sendLogKey,
                "metadata": {
                    "invoiceId": invoiceId,
                    "template": template,
                    "sentAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                },
            }).execute()
            logger.info(f"T-30 awareness email sent for invoice {invoiceId}")
        except Exception as e:
            logger.error(f"T-30 email send failed for invoice {invoiceId}: {e}")

    def _sendT7Email(self, user: dict, artifact: dict) -> None:
        """
        Send a T-7 payment ready email with the Razorpay pay link.

        Uses SubscriptionLog as an idempotent send-log keyed by
        invoiceId + template to ensure the email is sent exactly once.

        Args:
            user: User row (email, fullName, domainCount).
            artifact: The updated invoice row with shortUrl, total_amount.
        """
        template = "renewal_payment_ready_t7"
        invoiceId = artifact.get("id", "")
        userId = user.get("userId", "")
        sendLogKey = f"{invoiceId}:{template}"
        lockKey = f"renewal-email:{sendLogKey}"
        lockAcquired = self.redisClient.set(
            lockKey, "1", nx=True, ex=_EMAIL_LOCK_TTL_SECONDS
        )
        if not lockAcquired:
            return

        existingLog = (
            self.client.table("SubscriptionLog")
            .select("id")
            .eq("userId", userId)
            .eq("eventType", f"email.{template}")
            .eq("status", sendLogKey)
            .limit(1)
            .execute()
            .data
        )
        if existingLog:
            return

        emailUrl = os.environ.get("RENEWAL_REMINDER_EMAIL_URL")
        if not emailUrl:
            logger.info("T-7 email skipped: RENEWAL_REMINDER_EMAIL_URL not configured")
            return

        payload = {
            "email": user.get("email", ""),
            "name": user.get("fullName", ""),
            "template": template,
            "amount": artifact.get("total_amount", 0),
            "currency": artifact.get("currency", "INR"),
            "paymentUrl": artifact.get("shortUrl", ""),
            "dueDate": artifact.get("due_date", ""),
            "domainCount": user.get("domainCount", 0),
        }

        try:
            requests.post(
                url=emailUrl,
                data=json.dumps(payload),
                headers={"Authorization": f"Bearer {os.environ.get('SUPABASE_KEY', '')}"},
                timeout=10,
            )
            self.client.table("SubscriptionLog").insert({
                "userId": userId,
                "eventType": f"email.{template}",
                "status": sendLogKey,
                "metadata": {
                    "invoiceId": invoiceId,
                    "template": template,
                    "sentAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                },
            }).execute()
            logger.info(f"T-7 payment ready email sent for invoice {invoiceId}")
        except Exception as e:
            logger.error(f"T-7 email send failed for invoice {invoiceId}: {e}")
