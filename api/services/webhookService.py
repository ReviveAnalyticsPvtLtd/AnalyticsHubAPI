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
from utils.webhookExceptions import RetryableWebhookError
from utils.logger import logger
from api.commons import client
import datetime
import hashlib
import requests
import json
import hmac
import os



WEBHOOK_PROCESSING_TIMEOUT_MINUTES = int(os.environ.get("WEBHOOK_PROCESSING_TIMEOUT_MINUTES", "10"))

EVENT_HANDLERS = {
    "subscription.activated": "_handleSubscriptionActivated",
    "subscription.charged": "_handleSubscriptionCharged",
    "subscription.halted": "_handleSubscriptionHalted",
    "subscription.cancelled": "_handleSubscriptionCancelled",
    "subscription.paused": "_handleSubscriptionPaused",
    "subscription.resumed": "_handleSubscriptionResumed",
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
        eventId = event.get("event_id") or event.get("id", "")
        try:
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
        except RetryableWebhookError as e:
            try:
                if eventId:
                    self._markWebhookEventFailed(
                        eventId=eventId,
                        errorMessage=str(e)
                    )
            except Exception as markError:
                logger.error(f"Failed to mark webhook event as failed: {markError}")
            raise
        except Exception as e:
            try:
                if eventId:
                    self._markWebhookEventFailed(
                        eventId=eventId,
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
            .select("userId, email, fullName, pendingRemovals, pendingAdditions, subscribedExperts, domainCount") \
            .eq("razorpaySubscriptionId", subscriptionId) \
            .execute()
        return result.data[0] if result.data else None

    def _requireUserBySubscriptionId(self, subscriptionId: str, eventType: str) -> dict:
        """
        Resolve user by subscription ID or raise a retryable webhook error.

        Args:
            subscriptionId (str): Razorpay subscription ID from webhook payload.
            eventType (str): Webhook event type for logging context.

        Returns:
            dict: The resolved user record.
        """
        if not subscriptionId:
            raise RetryableWebhookError(f"Missing subscription_id for webhook event {eventType}")
        user = self._findUserBySubscriptionId(subscriptionId)
        if not user:
            raise RetryableWebhookError(
                f"No user found for subscription {subscriptionId} during {eventType}"
            )
        return user

    def _auditLog(self, userId: str, eventType: str, **kwargs) -> None:
        """
        Insert a row into the SubscriptionLog table.

        Args:
            userId (str): The user ID associated with this log entry.
            eventType (str): The event type (e.g. 'subscription.created', 'domain.add_requested').
            **kwargs: Optional fields — status, plus any key-value pairs to store in metadata.
                      Reserved keys: paymentId, subscriptionId, invoiceId, amount, currency
                      are auto-mapped into metadata with 'razorpay' prefix where applicable.
        """
        try:
            status = kwargs.pop("status", None)
            existingMeta = kwargs.pop("metadata", None) or {}

            metaFields = {}
            razorpayPrefixed = {"paymentId", "subscriptionId", "invoiceId"}
            for key in ("paymentId", "subscriptionId", "invoiceId", "amount", "currency"):
                val = kwargs.pop(key, None)
                if val is not None:
                    metaKey = f"razorpay{key[0].upper()}{key[1:]}" if key in razorpayPrefixed else key
                    metaFields[metaKey] = val

            metadata = {**metaFields, **existingMeta, **kwargs}

            self.client.table("SubscriptionLog").insert({
                "userId": userId,
                "eventType": eventType,
                "status": status,
                "metadata": metadata if metadata else None,
            }).execute()
        except Exception as e:
            logger.error(f"SubscriptionLog insert failed for user {userId}, event {eventType}: {e}")

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
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.activated")
        currentTime = datetime.datetime.utcnow()
        self.client.table("Users").update({
            "subscriptionStatus": "ACTIVE",
            "subscriptionStart": currentTime.isoformat(),
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.activated",
            subscriptionId=subscriptionId, status="ACTIVE"
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
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.charged")
        payload = event.get("payload", {})
        paymentEntity = payload.get("payment", {}).get("entity", {})
        subscriptionEntity = payload.get("subscription", {}).get("entity", {})
        currentPeriodEnd = subscriptionEntity.get("current_end")
        if currentPeriodEnd:
            expiry = datetime.datetime.utcfromtimestamp(currentPeriodEnd)
        else:
            expiry = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        updateData = {
            "subscriptionStatus": "ACTIVE",
            "subscriptionExpiry": expiry.isoformat(),
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
            status="CHARGED"
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
        self._upsertInvoiceRecord(
            userId=userId,
            invoiceData={
                "id": invoiceId,
                "subscription_id": subscriptionEntity.get("id"),
                "payment_id": paymentEntity.get("id"),
                "amount": paymentEntity.get("amount", 0),
                "currency": paymentEntity.get("currency", "INR"),
                "status": "paid",
                "billing_start": subscriptionEntity.get("current_start"),
                "billing_end": subscriptionEntity.get("current_end"),
                "paid_at": paymentEntity.get("captured_at") or paymentEntity.get("created_at"),
                "short_url": paymentEntity.get("short_url"),
            }
        )

    def _upsertInvoiceRecord(self, userId: str, invoiceData: dict) -> None:
        """
        Store or update an invoice record from webhook payload data.

        Args:
            userId (str): The user ID.
            invoiceData (dict): Invoice-shaped payload data.
        """
        billingStart = invoiceData.get("billing_start")
        billingEnd = invoiceData.get("billing_end")
        paidAt = invoiceData.get("paid_at")
        try:
            self.client.table("Invoices").upsert({
                "userId": userId,
                "razorpayInvoiceId": invoiceData.get("id"),
                "razorpaySubscriptionId": invoiceData.get("subscription_id"),
                "razorpayPaymentId": invoiceData.get("payment_id"),
                "amount": invoiceData.get("amount", 0),
                "currency": invoiceData.get("currency", "INR"),
                "status": invoiceData.get("status", "paid").upper(),
                "billingStart": str(datetime.datetime.utcfromtimestamp(billingStart)) if billingStart else None,
                "billingEnd": str(datetime.datetime.utcfromtimestamp(billingEnd)) if billingEnd else None,
                "paidAt": str(datetime.datetime.utcfromtimestamp(paidAt)) if paidAt else None,
                "shortUrl": invoiceData.get("short_url"),
            }, on_conflict="razorpayInvoiceId").execute()
        except Exception as e:
            logger.error(f"Failed to upsert invoice {invoiceData.get('id')} for user {userId}: {e}")

    def _handleSubscriptionHalted(self, event: dict) -> None:
        """
        Handle the subscription.halted webhook event.

        Sets subscription status to expired immediately (no grace period).

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.halted")
        self.client.table("Users").update({
            "subscriptionStatus": "EXPIRED",
            "subscriptionDaysLeft": -1,
        }).eq("userId", user["userId"]).execute()
        pendingAdditions = user.get("pendingAdditions") or []
        failedAny = False
        for item in pendingAdditions:
            if item.get("state") in ("processing", "awaiting_payment"):
                item["state"] = "failed"
                failedAny = True
        if failedAny:
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", user["userId"]).execute()
            for item in pendingAdditions:
                if item.get("state") == "failed":
                    try:
                        self._auditLog(
                            user["userId"], "domain.add_failed",
                            status="FAILED",
                            metadata={
                                "domain": item["domain"],
                                "previousQuantity": item.get("currentQuantity", 0),
                                "newQuantity": item.get("currentQuantity", 0),
                                "proratedAmount": 0,
                                "effectiveAt": "none"
                            }
                        )
                    except Exception as logErr:
                        logger.error(f"SubscriptionLog insert failed for halted pending addition: {logErr}")
        self._auditLog(
            user["userId"], "subscription.halted",
            subscriptionId=subscriptionId, status="EXPIRED"
        )
        self._sendPaymentFailureEmail(user["email"], user["fullName"], "subscription_halted")
        logger.info(f"Subscription expired immediately for user {user['userId']} (halted, no grace period)")

    def _handleSubscriptionCancelled(self, event: dict) -> None:
        """
        Handle the subscription.cancelled webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.cancelled")
        self.client.table("Users").update({
            "subscriptionStatus": "CANCELLED",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.cancelled",
            subscriptionId=subscriptionId, status="CANCELLED"
        )
        logger.info(f"Subscription cancelled for user {user['userId']}")

    def _handleSubscriptionPaused(self, event: dict) -> None:
        """
        Handle the subscription.paused webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.paused")
        self.client.table("Users").update({
            "subscriptionStatus": "PAUSED",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.paused",
            subscriptionId=subscriptionId, status="PAUSED"
        )
        logger.info(f"Subscription paused for user {user['userId']}")

    def _handleSubscriptionResumed(self, event: dict) -> None:
        """
        Handle the subscription.resumed webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.resumed")
        self.client.table("Users").update({
            "subscriptionStatus": "ACTIVE",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.resumed",
            subscriptionId=subscriptionId, status="ACTIVE"
        )
        logger.info(f"Subscription resumed for user {user['userId']}")


    def _handleSubscriptionCompleted(self, event: dict) -> None:
        """
        Handle the subscription.completed webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.completed")
        self.client.table("Users").update({
            "subscriptionStatus": "EXPIRED",
        }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.completed",
            subscriptionId=subscriptionId, status="EXPIRED"
        )
        logger.info(f"Subscription completed for user {user['userId']}")

    def _handleSubscriptionUpdated(self, event: dict) -> None:
        """
        Handle the subscription.updated webhook event.

        Activates pending domain additions when Razorpay confirms a quantity
        increase. Skips activation if quantity decreased or is unchanged
        relative to active domain count (e.g. removal events).

        Args:
            event (dict): The Razorpay webhook event.
        """
        subscriptionId = self._extractSubscriptionId(event)
        user = self._requireUserBySubscriptionId(subscriptionId, "subscription.updated")
        subscriptionEntity = event.get("payload", {}).get("subscription", {}).get("entity", {})
        newQuantity = subscriptionEntity.get("quantity")
        if newQuantity is None:
            logger.info(f"Subscription updated for user {user['userId']} but no quantity in payload, skipping.")
            return
        currentDomainCount = user.get("domainCount") or 0
        pendingAdditions = user.get("pendingAdditions") or []
        pendingRemovals = user.get("pendingRemovals") or []
        experts = user.get("subscribedExperts") or ""
        currentExperts = [e.strip() for e in experts.split(",") if e.strip()]
        activationCount = newQuantity - currentDomainCount
        if activationCount > 0 and pendingAdditions:
            activatable = [
                item for item in pendingAdditions
                if item.get("state") in ("processing", "awaiting_payment", "scheduled_cycle_end")
            ]
            activatable.sort(key=lambda x: x.get("requestedAt", ""))
            toActivate = activatable[:activationCount]
            activatedDomains = []
            for item in toActivate:
                domain = item["domain"]
                if domain not in currentExperts:
                    currentExperts.append(domain)
                    activatedDomains.append(domain)
                item["state"] = "activated"
            remainingPending = [
                item for item in pendingAdditions
                if item.get("state") != "activated"
            ]
            updatedDomainCount = len(currentExperts)
            self.client.table("Users").update({
                "subscribedExperts": ", ".join(currentExperts),
                "domainCount": updatedDomainCount,
                "pendingAdditions": remainingPending,
            }).eq("userId", user["userId"]).execute()
            for domain in activatedDomains:
                try:
                    self._auditLog(
                        user["userId"], "domain.add_activated",
                        status="ACTIVATED",
                        metadata={
                            "domain": domain,
                            "previousQuantity": currentDomainCount,
                            "newQuantity": updatedDomainCount,
                            "proratedAmount": 0,
                            "effectiveAt": "now"
                        }
                    )
                except Exception as logErr:
                    logger.error(f"SubscriptionLog insert failed for activated domain {domain}: {logErr}")
            if pendingRemovals:
                scheduledQuantity = updatedDomainCount - len(pendingRemovals)
                if scheduledQuantity >= 1:
                    try:
                        import razorpay
                        import os
                        rzpClient = razorpay.Client(
                            auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
                        )
                        rzpClient.subscription.edit(subscriptionId, {
                            "quantity": scheduledQuantity,
                            "schedule_change_at": "cycle_end"
                        })
                        logger.info(f"Re-scheduled pending removals after activation for user {user['userId']}: qty {scheduledQuantity}")
                    except Exception as rzpErr:
                        logger.error(f"Failed to re-schedule pending removals for user {user['userId']}: {rzpErr}")
            logger.info(f"Activated domains {activatedDomains} for user {user['userId']}, new count: {updatedDomainCount}")
        else:
            self.client.table("Users").update({
                "domainCount": newQuantity,
            }).eq("userId", user["userId"]).execute()
        self._auditLog(
            user["userId"], "subscription.updated",
            subscriptionId=subscriptionId,
            status="UPDATED",
            metadata={"new_quantity": newQuantity, "activation_count": max(activationCount, 0) if pendingAdditions else 0}
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
            status="AUTHORIZED"
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
            status="CAPTURED"
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
            status="FAILED",
            metadata={"error_code": errorMeta, "error_description": paymentEntity.get("error_description", "")}
        )
        if user:
            pendingAdditions = user.get("pendingAdditions") or []
            failedAny = False
            for item in pendingAdditions:
                if item.get("state") in ("processing", "awaiting_payment"):
                    item["state"] = "failed"
                    failedAny = True
            if failedAny:
                self.client.table("Users").update({
                    "pendingAdditions": pendingAdditions
                }).eq("userId", user["userId"]).execute()
                for item in pendingAdditions:
                    if item.get("state") == "failed":
                        try:
                            self._auditLog(
                                user["userId"], "domain.add_failed",
                                status="FAILED",
                                metadata={
                                    "domain": item["domain"],
                                    "previousQuantity": item.get("currentQuantity", 0),
                                    "newQuantity": item.get("currentQuantity", 0),
                                    "proratedAmount": 0,
                                    "effectiveAt": "none"
                                }
                            )
                        except Exception as logErr:
                            logger.error(f"SubscriptionLog insert failed for failed pending addition: {logErr}")
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
        user = self._requireUserBySubscriptionId(subscriptionId, "invoice.paid")
        self._upsertInvoiceRecord(userId=user["userId"], invoiceData=invoiceEntity)
        pendingAdditions = user.get("pendingAdditions") or []
        enriched = False
        for item in pendingAdditions:
            if item.get("state") == "processing":
                item["invoiceId"] = invoiceEntity.get("id")
                item["paymentId"] = invoiceEntity.get("payment_id")
                shortUrl = invoiceEntity.get("short_url")
                if shortUrl:
                    item["checkoutUrl"] = shortUrl
                    item["state"] = "awaiting_payment"
                enriched = True
                break
        if enriched:
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", user["userId"]).execute()
            logger.info(f"Enriched pending addition with invoice data for user {user['userId']}")
        self._auditLog(
            user["userId"], "invoice.paid",
            invoiceId=invoiceEntity.get("id"),
            subscriptionId=subscriptionId,
            paymentId=invoiceEntity.get("payment_id"),
            amount=invoiceEntity.get("amount"),
            currency=invoiceEntity.get("currency", "INR"),
            status="PAID"
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
