"""
webhookService.py

This module provides the WebhookService class, which handles Razorpay
webhook signature verification, idempotent event processing, and
dispatching to specific handlers for subscription, payment, invoice,
and refund events.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["webhookService"]


from utils.exceptionHandler import CustomException
from utils.logger import logger
from api.commons import client
import datetime
import hashlib
import requests
import json
import hmac
import os


GRACE_PERIOD_DAYS = int(os.environ.get("GRACE_PERIOD_DAYS", "3"))
WEBHOOK_PROCESSING_TIMEOUT_MINUTES = int(os.environ.get("WEBHOOK_PROCESSING_TIMEOUT_MINUTES", "10"))

EVENT_HANDLERS = {
    "subscription.activated": "_handleSubscriptionActivated",
    "subscription.charged": "_handleSubscriptionCharged",
    "subscription.halted": "_handleSubscriptionHalted",
    "subscription.cancelled": "_handleSubscriptionCancelled",
    "subscription.paused": "_handleSubscriptionPaused",
    "subscription.resumed": "_handleSubscriptionResumed",
    "subscription.pending": "_handleSubscriptionPending",
    "subscription.completed": "_handleSubscriptionCompleted",
    "subscription.updated": "_handleSubscriptionUpdated",
    "payment.authorized": "_handlePaymentAuthorized",
    "payment.captured": "_handlePaymentCaptured",
    "payment.failed": "_handlePaymentFailed",
    "invoice.paid": "_handleInvoicePaid",
    "refund.processed": "_handleRefundProcessed",
}


class WebhookService:
    """
    Service class for Razorpay webhook processing.

    Handles signature verification, idempotency checks, and event
    dispatching for all supported Razorpay webhook events.
    """

    def __init__(self) -> None:
        """
        Initialize the WebhookService.
        """
        logger.info("Initializing Webhook Service.")
        self.client = client

    def verifyWebhookSignature(self, requestBody: bytes, signature: str) -> bool:
        """
        Verify the Razorpay webhook signature using HMAC SHA-256.

        Args:
            requestBody (bytes): The raw request body bytes.
            signature (str): The X-Razorpay-Signature header value.

        Returns:
            bool: True if the signature is valid, False otherwise.
        """
        try:
            webhookSecret = os.environ["RAZORPAY_WEBHOOK_SECRET"]
            expectedSignature = hmac.new(
                webhookSecret.encode(),
                requestBody,
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expectedSignature, signature)
        except Exception as e:
            logger.error(f"Webhook signature verification error: {e}")
            return False

    def processEvent(self, event: dict) -> None:
        """
        Process a Razorpay webhook event with idempotency.

        Args:
            event (dict): The parsed Razorpay webhook event payload.
        """
        try:
            eventId = event.get("event_id") or event.get("id", "")
            eventType = event.get("event", "")
            if not eventId or not eventType:
                logger.error("Webhook event missing event_id or event type.")
                return
            if not self._claimWebhookEvent(eventId=eventId, eventType=eventType, event=event):
                return
            handlerName = EVENT_HANDLERS.get(eventType)
            if handlerName:
                handler = getattr(self, handlerName)
                handler(event)
            else:
                logger.info(f"Unhandled webhook event type: {eventType}")
            self._markWebhookEventCompleted(eventId=eventId)
        except Exception as e:
            try:
                if event.get("event_id") or event.get("id", ""):
                    self._markWebhookEventFailed(
                        eventId=event.get("event_id") or event.get("id", ""),
                        errorMessage=str(e)
                    )
            except Exception as markError:
                logger.error(f"Failed to mark webhook event as failed: {markError}")
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def _claimWebhookEvent(self, eventId: str, eventType: str, event: dict) -> bool:
        """
        Atomically claim a webhook event for processing.

        Returns True only for the worker that owns processing rights.
        Returns False for duplicates already completed or being processed.
        """
        nowTime = datetime.datetime.utcnow()
        nowIso = str(nowTime)
        try:
            self.client.table("WebhookEvents").insert({
                "razorpayEventId": eventId,
                "eventType": eventType,
                "payload": event,
                "status": "processing",
                "attempts": 1,
                "lastAttemptAt": nowIso,
                "errorMessage": None,
            }).execute()
            logger.info(f"Webhook event claimed for processing: {eventId}")
            return True
        except Exception as insertError:
            if not self._isUniqueViolation(insertError):
                raise insertError
            existingResult = self.client.table("WebhookEvents") \
                .select("status, attempts, lastAttemptAt") \
                .eq("razorpayEventId", eventId) \
                .limit(1) \
                .execute()
            if not existingResult.data:
                logger.warning(f"Webhook duplicate claim conflict with missing row: {eventId}")
                return False
            existingEvent = existingResult.data[0]
            status = existingEvent.get("status")
            attempts = int(existingEvent.get("attempts") or 1)
            lastAttemptAt = existingEvent.get("lastAttemptAt")
            if status == "completed":
                logger.info(f"Duplicate webhook event skipped (already completed): {eventId}")
                return False
            if status == "processing":
                if not self._isStaleProcessing(lastAttemptAt=lastAttemptAt, nowTime=nowTime):
                    logger.info(f"Duplicate webhook event skipped (already processing): {eventId}")
                    return False
                reclaimResult = self.client.table("WebhookEvents").update({
                    "status": "processing",
                    "attempts": attempts + 1,
                    "lastAttemptAt": nowIso,
                    "errorMessage": None,
                    "payload": event,
                    "eventType": eventType,
                }).eq("razorpayEventId", eventId) \
                 .eq("status", "processing") \
                 .eq("lastAttemptAt", lastAttemptAt) \
                 .execute()
                if reclaimResult.data:
                    logger.warning(f"Reclaimed stale webhook processing lock: {eventId}")
                    return True
                logger.info(f"Stale webhook lock reclaim lost to another worker: {eventId}")
                return False
            if status == "failed":
                retryResult = self.client.table("WebhookEvents").update({
                    "status": "processing",
                    "attempts": attempts + 1,
                    "lastAttemptAt": nowIso,
                    "errorMessage": None,
                    "payload": event,
                    "eventType": eventType,
                }).eq("razorpayEventId", eventId) \
                 .eq("status", "failed") \
                 .execute()
                if retryResult.data:
                    logger.info(f"Retrying failed webhook event: {eventId}, attempt={attempts + 1}")
                    return True
                logger.info(f"Failed webhook retry lost to another worker: {eventId}")
                return False
            logger.info(f"Webhook event skipped with unsupported status '{status}': {eventId}")
            return False

    def _markWebhookEventCompleted(self, eventId: str) -> None:
        """
        Mark a webhook event as completed after successful processing.
        """
        self.client.table("WebhookEvents").update({
            "status": "completed",
            "completedAt": str(datetime.datetime.utcnow()),
            "errorMessage": None,
        }).eq("razorpayEventId", eventId).execute()

    def _markWebhookEventFailed(self, eventId: str, errorMessage: str) -> None:
        """
        Mark a webhook event as failed to enable replay-based retries.
        """
        self.client.table("WebhookEvents").update({
            "status": "failed",
            "errorMessage": errorMessage[:2000],
            "lastAttemptAt": str(datetime.datetime.utcnow()),
        }).eq("razorpayEventId", eventId).execute()

    @staticmethod
    def _isUniqueViolation(error: Exception) -> bool:
        """
        Detect duplicate key violations from Postgres/Supabase errors.
        """
        errorMessage = str(error).lower()
        return "duplicate key" in errorMessage or "unique constraint" in errorMessage or "23505" in errorMessage

    @staticmethod
    def _isStaleProcessing(lastAttemptAt: str | None, nowTime: datetime.datetime) -> bool:
        """
        Determine whether a processing lock is stale and safe to reclaim.
        """
        if not lastAttemptAt:
            return True
        try:
            normalized = lastAttemptAt.replace("Z", "+00:00")
            lastAttempt = datetime.datetime.fromisoformat(normalized)
            if lastAttempt.tzinfo is not None:
                lastAttempt = lastAttempt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            delta = nowTime - lastAttempt
            return delta.total_seconds() > WEBHOOK_PROCESSING_TIMEOUT_MINUTES * 60
        except Exception:
            return True

    def _findUserBySubscriptionId(self, subscriptionId: str) -> dict | None:
        """
        Look up a user by their Razorpay subscription ID.

        Args:
            subscriptionId (str): The Razorpay subscription ID.

        Returns:
            dict | None: The user record or None if not found.
        """
        result = self.client.table("Users") \
            .select("userId, email, fullName, pendingRemovals, subscribedExperts, domainCount") \
            .eq("razorpaySubscriptionId", subscriptionId) \
            .execute()
        return result.data[0] if result.data else None

    def _auditLog(self, userId: str, action: str, **kwargs) -> None:
        """
        Insert a row into the PaymentAuditLog table.

        Args:
            userId (str): The user ID associated with this audit entry.
            action (str): The action being logged.
            **kwargs: Optional fields — paymentId, subscriptionId, invoiceId,
                      amount, currency, status, metadata.
        """
        try:
            self.client.table("PaymentAuditLog").insert({
                "userId": userId,
                "action": action,
                "razorpayPaymentId": kwargs.get("paymentId"),
                "razorpaySubscriptionId": kwargs.get("subscriptionId"),
                "razorpayInvoiceId": kwargs.get("invoiceId"),
                "amount": kwargs.get("amount"),
                "currency": kwargs.get("currency", "INR"),
                "status": kwargs.get("status"),
                "metadata": kwargs.get("metadata"),
            }).execute()
        except Exception as e:
            logger.error(f"Audit log insert failed for user {userId}, action {action}: {e}")

    def _extractSubscriptionId(self, event: dict) -> str:
        """
        Extract the Razorpay subscription ID from a webhook event payload.

        Args:
            event (dict): The Razorpay webhook event.

        Returns:
            str: The subscription ID.
        """
        payload = event.get("payload", {})
        subscription = payload.get("subscription", {}).get("entity", {})
        return subscription.get("id", "")

    def _handleSubscriptionActivated(self, event: dict) -> None:
        """
        Handle the subscription.activated webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        currentTime = datetime.datetime.utcnow()
        self.client.table("Users").update({
            "subscriptionStatus": "active",
            "subscriptionStart": str(currentTime),
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.activated",
            subscriptionId=subscriptionId, status="active"
        )
        logger.info(f"Subscription activated for user {user['userId']}")

    def _handleSubscriptionCharged(self, event: dict) -> None:
        """
        Handle the subscription.charged webhook event.

        Extends subscription expiry based on billing period and stores
        the invoice record.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        payload = event.get("payload", {})
        paymentEntity = payload.get("payment", {}).get("entity", {})
        subscriptionEntity = payload.get("subscription", {}).get("entity", {})
        currentPeriodEnd = subscriptionEntity.get("current_end")
        if currentPeriodEnd:
            expiry = datetime.datetime.utcfromtimestamp(currentPeriodEnd)
        else:
            expiry = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        updateData = {
            "subscriptionStatus": "active",
            "subscriptionExpiry": str(expiry),
            "gracePeriodEnd": None,
        }
        daysLeft = (expiry.date() - datetime.datetime.utcnow().date()).days
        updateData["subscriptionDaysLeft"] = max(daysLeft, 0)
        self.client.table("Users").update(updateData).eq("userId", user["userId"]).execute()
        invoiceId = paymentEntity.get("invoice_id")
        if invoiceId:
            self._storeInvoiceFromCharge(user["userId"], paymentEntity, subscriptionEntity)
        self._auditLog(
            user["userId"], "subscription.charged",
            subscriptionId=subscriptionId,
            paymentId=paymentEntity.get("id"),
            amount=paymentEntity.get("amount"),
            currency=paymentEntity.get("currency", "INR"),
            status="charged"
        )
        pendingRemovals = user.get("pendingRemovals") or []
        if pendingRemovals:
            experts = user.get("subscribedExperts") or ""
            currentExperts = [e.strip() for e in experts.split(",") if e.strip()]
            updatedExperts = [e for e in currentExperts if e not in pendingRemovals]
            self.client.table("Users").update({
                "subscribedExperts": ", ".join(updatedExperts),
                "domainCount": len(updatedExperts),
                "pendingRemovals": []
            }).eq("userId", user["userId"]).execute()
            logger.info(f"Processed pending removals for user {user['userId']}: {pendingRemovals}")
        logger.info(f"Subscription charged for user {user['userId']}, expiry extended to {expiry}")

    def _storeInvoiceFromCharge(self, userId: str, paymentEntity: dict, subscriptionEntity: dict) -> None:
        """
        Store an invoice record from a subscription.charged event.

        Args:
            userId (str): The user ID.
            paymentEntity (dict): The payment entity from the webhook payload.
            subscriptionEntity (dict): The subscription entity from the webhook payload.
        """
        invoiceId = paymentEntity.get("invoice_id")
        if not invoiceId:
            return
        billingStart = subscriptionEntity.get("current_start")
        billingEnd = subscriptionEntity.get("current_end")
        paidAt = paymentEntity.get("captured_at") or paymentEntity.get("created_at")
        try:
            self.client.table("Invoices").insert({
                "userId": userId,
                "razorpayInvoiceId": invoiceId,
                "razorpaySubscriptionId": subscriptionEntity.get("id"),
                "razorpayPaymentId": paymentEntity.get("id"),
                "amount": paymentEntity.get("amount", 0),
                "currency": paymentEntity.get("currency", "INR"),
                "status": "paid",
                "billingStart": str(datetime.datetime.utcfromtimestamp(billingStart)) if billingStart else None,
                "billingEnd": str(datetime.datetime.utcfromtimestamp(billingEnd)) if billingEnd else None,
                "paidAt": str(datetime.datetime.utcfromtimestamp(paidAt)) if paidAt else None,
                "shortUrl": paymentEntity.get("short_url"),
            }).execute()
        except Exception as e:
            logger.error(f"Failed to store invoice {invoiceId} for user {userId}: {e}")

    def _handleSubscriptionHalted(self, event: dict) -> None:
        """
        Handle the subscription.halted webhook event.

        Sets subscription status to halted and initiates grace period.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        gracePeriodEnd = datetime.datetime.utcnow() + datetime.timedelta(days=GRACE_PERIOD_DAYS)
        self.client.table("Users").update({
            "subscriptionStatus": "halted",
            "gracePeriodEnd": str(gracePeriodEnd),
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.halted",
            subscriptionId=subscriptionId, status="halted"
        )
        self._sendPaymentFailureEmail(user["email"], user["fullName"], "subscription_halted")
        logger.info(f"Subscription halted for user {user['userId']}, grace period until {gracePeriodEnd}")

    def _handleSubscriptionCancelled(self, event: dict) -> None:
        """
        Handle the subscription.cancelled webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        self.client.table("Users").update({
            "subscriptionStatus": "cancelled",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.cancelled",
            subscriptionId=subscriptionId, status="cancelled"
        )
        logger.info(f"Subscription cancelled for user {user['userId']}")

    def _handleSubscriptionPaused(self, event: dict) -> None:
        """
        Handle the subscription.paused webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        self.client.table("Users").update({
            "subscriptionStatus": "paused",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.paused",
            subscriptionId=subscriptionId, status="paused"
        )
        logger.info(f"Subscription paused for user {user['userId']}")

    def _handleSubscriptionResumed(self, event: dict) -> None:
        """
        Handle the subscription.resumed webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        self.client.table("Users").update({
            "subscriptionStatus": "active",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.resumed",
            subscriptionId=subscriptionId, status="active"
        )
        logger.info(f"Subscription resumed for user {user['userId']}")

    def _handleSubscriptionPending(self, event: dict) -> None:
        """
        Handle the subscription.pending webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        self.client.table("Users").update({
            "subscriptionStatus": "pending",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.pending",
            subscriptionId=subscriptionId, status="pending"
        )
        logger.info(f"Subscription pending for user {user['userId']}")

    def _handleSubscriptionCompleted(self, event: dict) -> None:
        """
        Handle the subscription.completed webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        self.client.table("Users").update({
            "subscriptionStatus": "expired",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.completed",
            subscriptionId=subscriptionId, status="expired"
        )
        logger.info(f"Subscription completed for user {user['userId']}")

    def _handleSubscriptionUpdated(self, event: dict) -> None:
        """
        Handle the subscription.updated webhook event.

        Syncs the local domainCount with the quantity reported by Razorpay
        after a subscription edit (add/remove domain).

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            logger.error(f"No user found for subscription {subscriptionId}")
            return
        subscriptionEntity = event.get("payload", {}).get("subscription", {}).get("entity", {})
        newQuantity = subscriptionEntity.get("quantity")
        if newQuantity is not None:
            self.client.table("Users").update({
                "domainCount": newQuantity,
            }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.updated",
            subscriptionId=subscriptionId,
            status="updated",
            metadata={"new_quantity": newQuantity}
        )
        logger.info(f"Subscription updated for user {user['userId']}, quantity: {newQuantity}")

    def _handlePaymentAuthorized(self, event: dict) -> None:
        """
        Handle the payment.authorized webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        paymentEntity = event.get("payload", {}).get("payment", {}).get("entity", {})
        subscriptionId = paymentEntity.get("subscription_id", "")
        user = self._findUserBySubscriptionId(subscriptionId) if subscriptionId else None
        userId = user["userId"] if user else "unknown"
        self._auditLog(
            userId, "payment.authorized",
            paymentId=paymentEntity.get("id"),
            subscriptionId=subscriptionId,
            amount=paymentEntity.get("amount"),
            currency=paymentEntity.get("currency", "INR"),
            status="authorized"
        )
        logger.info(f"Payment authorized: {paymentEntity.get('id')}")

    def _handlePaymentCaptured(self, event: dict) -> None:
        """
        Handle the payment.captured webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        paymentEntity = event.get("payload", {}).get("payment", {}).get("entity", {})
        subscriptionId = paymentEntity.get("subscription_id", "")
        user = self._findUserBySubscriptionId(subscriptionId) if subscriptionId else None
        userId = user["userId"] if user else "unknown"
        self._auditLog(
            userId, "payment.captured",
            paymentId=paymentEntity.get("id"),
            subscriptionId=subscriptionId,
            amount=paymentEntity.get("amount"),
            currency=paymentEntity.get("currency", "INR"),
            status="captured"
        )
        logger.info(f"Payment captured: {paymentEntity.get('id')}")

    def _handlePaymentFailed(self, event: dict) -> None:
        """
        Handle the payment.failed webhook event.

        Logs the failure and sends a notification email to the user.

        Args:
            event (dict): The Razorpay webhook event.
        """
        paymentEntity = event.get("payload", {}).get("payment", {}).get("entity", {})
        subscriptionId = paymentEntity.get("subscription_id", "")
        user = self._findUserBySubscriptionId(subscriptionId) if subscriptionId else None
        userId = user["userId"] if user else "unknown"
        errorMeta = paymentEntity.get("error_code", "")
        self._auditLog(
            userId, "payment.failed",
            paymentId=paymentEntity.get("id"),
            subscriptionId=subscriptionId,
            amount=paymentEntity.get("amount"),
            currency=paymentEntity.get("currency", "INR"),
            status="failed",
            metadata={"error_code": errorMeta, "error_description": paymentEntity.get("error_description", "")}
        )
        if user:
            self._sendPaymentFailureEmail(user["email"], user["fullName"], "payment_failed")
        logger.info(f"Payment failed: {paymentEntity.get('id')}")

    def _handleInvoicePaid(self, event: dict) -> None:
        """
        Handle the invoice.paid webhook event.

        Stores the invoice record in the Invoices table.

        Args:
            event (dict): The Razorpay webhook event.
        """
        invoiceEntity = event.get("payload", {}).get("invoice", {}).get("entity", {})
        subscriptionId = invoiceEntity.get("subscription_id", "")
        user = self._findUserBySubscriptionId(subscriptionId) if subscriptionId else None
        if not user:
            logger.error(f"No user found for invoice subscription {subscriptionId}")
            return
        billingStart = invoiceEntity.get("billing_start")
        billingEnd = invoiceEntity.get("billing_end")
        paidAt = invoiceEntity.get("paid_at")
        try:
            self.client.table("Invoices").insert({
                "userId": user["userId"],
                "razorpayInvoiceId": invoiceEntity.get("id"),
                "razorpaySubscriptionId": subscriptionId,
                "razorpayPaymentId": invoiceEntity.get("payment_id"),
                "amount": invoiceEntity.get("amount", 0),
                "currency": invoiceEntity.get("currency", "INR"),
                "status": invoiceEntity.get("status", "paid"),
                "billingStart": str(datetime.datetime.utcfromtimestamp(billingStart)) if billingStart else None,
                "billingEnd": str(datetime.datetime.utcfromtimestamp(billingEnd)) if billingEnd else None,
                "paidAt": str(datetime.datetime.utcfromtimestamp(paidAt)) if paidAt else None,
                "shortUrl": invoiceEntity.get("short_url"),
            }).execute()
        except Exception as e:
            logger.error(f"Failed to store invoice from webhook: {e}")
        self._auditLog(
            user["userId"], "invoice.paid",
            invoiceId=invoiceEntity.get("id"),
            subscriptionId=subscriptionId,
            paymentId=invoiceEntity.get("payment_id"),
            amount=invoiceEntity.get("amount"),
            currency=invoiceEntity.get("currency", "INR"),
            status="paid"
        )
        logger.info(f"Invoice paid stored for user {user['userId']}")

    def _handleRefundProcessed(self, event: dict) -> None:
        """
        Handle the refund.processed webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        refundEntity = event.get("payload", {}).get("refund", {}).get("entity", {})
        paymentId = refundEntity.get("payment_id", "")
        self._auditLog(
            "system", "refund.processed",
            paymentId=paymentId,
            amount=refundEntity.get("amount"),
            currency=refundEntity.get("currency", "INR"),
            status="processed",
            metadata={"refund_id": refundEntity.get("id")}
        )
        logger.info(f"Refund processed for payment {paymentId}")

    @staticmethod
    def _sendPaymentFailureEmail(email: str, name: str, reason: str) -> None:
        """
        Send a payment failure notification email via edge function.

        Args:
            email (str): The user's email address.
            name (str): The user's full name.
            reason (str): The failure reason identifier.
        """
        emailUrl = os.environ.get("PAYMENT_FAILED_EMAIL_URL")
        if not emailUrl:
            logger.info("PAYMENT_FAILED_EMAIL_URL not configured, skipping failure email.")
            return
        try:
            requests.post(
                url=emailUrl,
                data=json.dumps({
                    "email": email,
                    "name": name,
                    "reason": reason
                }),
                headers={
                    "Authorization": f"Bearer {os.environ['SUPABASE_KEY']}"
                },
                timeout=10
            )
        except Exception as e:
            logger.error(f"Failed to send payment failure email to {email}: {e}")


webhookService = WebhookService()
