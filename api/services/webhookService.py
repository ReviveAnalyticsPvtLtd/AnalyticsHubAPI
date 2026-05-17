"""
webhookService.py

This module provides the WebhookService class, which handles Razorpay
webhook signature verification, idempotent event processing, and
dispatching to specific handlers for payment, token, invoice,
and refund events.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["webhookService"]


from dateutil.relativedelta import relativedelta
from utils.exceptionHandler import CustomException
from utils.webhookExceptions import RetryableWebhookError
from utils.logger import logger
from api.commons import client
import datetime
from dateutil import parser
import hashlib
import requests
import json
import hmac
import os



WEBHOOK_PROCESSING_TIMEOUT_MINUTES = int(os.environ.get("WEBHOOK_PROCESSING_TIMEOUT_MINUTES", "10"))
RENEWAL_EMAIL_MAX_SEND_ATTEMPTS = int(os.environ.get("RENEWAL_EMAIL_MAX_SEND_ATTEMPTS", "3"))
RENEWAL_EMAIL_SINGLE_SEND_TEMPLATES = {"payment_success"}

EVENT_HANDLERS = {
    "payment.authorized": "_handlePaymentAuthorized",
    "payment.captured": "_handlePaymentCaptured",
    "order.paid": "_handleOrderPaid",
    "payment.failed": "_handlePaymentFailed",
    "invoice.paid": "_handleInvoicePaid",
    "invoice.expired": "_handleInvoiceExpired",
    "payment_link.paid": "_handlePaymentLinkPaid",
    "payment_link.expired": "_handlePaymentLinkExpired",
    "refund.processed": "_handleRefundProcessed",
    "token.confirmed": "_handleTokenConfirmed",
    "token.cancelled": "_handleTokenCancelled",
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

    def processEvent(self, event: dict, eventId: str = None) -> None:
        """
        Process a Razorpay webhook event with idempotency.

        Args:
            event (dict): The parsed Razorpay webhook event payload.
            eventId (str): Event ID from the X-Razorpay-Event-Id header.
        """
        eventId = eventId or event.get("event_id") or event.get("id", "")
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
            lastAttempt = parser.isoparse(normalized)
            if lastAttempt.tzinfo is not None:
                lastAttempt = lastAttempt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            delta = nowTime - lastAttempt
            return delta.total_seconds() > WEBHOOK_PROCESSING_TIMEOUT_MINUTES * 60
        except Exception:
            return True

    def _findUserById(self, userId: str) -> dict | None:
        """
        Look up a user by their internal user ID.

        Args:
            userId (str): The internal user ID.

        Returns:
            dict | None: The user record or None if not found.
        """
        result = self.client.table("Users") \
            .select("userId, email, fullName, subscriptionAnchorDay, recurringFailures, razorpayCustomerId, "
                    "pendingRemovals, pendingAdditions, subscribedExperts, domainCount") \
            .eq("userId", userId).execute()
        return result.data[0] if result.data else None

    def _findSubscriptionByUserId(self, userId: str) -> dict | None:
        """
        Look up canonical subscription row by internal user ID.

        Args:
            userId (str): The internal user ID.

        Returns:
            dict | None: The subscription record or None if not found.
        """
        result = self.client.table("subscriptions") \
            .select("id, user_id, billing_mode, status, current_period_start, current_period_end, renewal_due_at") \
            .eq("user_id", userId) \
            .order("updated_at", desc=True) \
            .limit(1) \
            .execute()
        return result.data[0] if result.data else None

    def _findUserByCustomerId(self, customerId: str) -> dict | None:
        """
        Look up a user by their Razorpay customer ID.

        Args:
            customerId (str): The Razorpay customer ID.

        Returns:
            dict | None: The user record or None if not found.
        """
        result = self.client.table("Users") \
            .select("userId, email, fullName, razorpayTokenId, razorpayCustomerId") \
            .eq("razorpayCustomerId", customerId).execute()
        return result.data[0] if result.data else None

    def _findInvoiceById(self, invoiceId: str) -> dict | None:
        """
        Look up an internal invoice by primary key.
        """
        result = self.client.table("Invoices") \
            .select("id, userId, total_amount, amount, currency, status, razorpay_order_id") \
            .eq("id", invoiceId) \
            .limit(1) \
            .execute()
        return result.data[0] if result.data else None

    def _markInvoicePaid(self, invoiceId: str, paymentEntity: dict) -> None:
        """
        Mark an internal invoice as paid from webhook payment entity.
        """
        updateData = {
            "status": "PAID",
            "razorpayPaymentId": paymentEntity.get("id"),
            "amount": paymentEntity.get("amount", 0),
            "currency": paymentEntity.get("currency", "INR"),
        }
        capturedAt = paymentEntity.get("captured_at") or paymentEntity.get("created_at")
        if capturedAt:
            updateData["paidAt"] = str(datetime.datetime.utcfromtimestamp(capturedAt))
        self.client.table("Invoices").update(updateData).eq("id", invoiceId).execute()

    def _markInvoiceFailed(self, invoiceId: str, errorCode: str = "", errorDescription: str = "") -> None:
        """
        Mark an internal invoice as failed from webhook failure signal.
        """
        self.client.table("Invoices").update({
            "status": "FAILED",
            "metadata_json": {
                "error_code": errorCode,
                "error_description": errorDescription,
                "updated_by": "webhook_payment_failed",
            }
        }).eq("id", invoiceId).execute()

    def _markHostedArtifactExpired(
        self,
        invoiceId: str,
        *,
        artifactType: str,
        artifactId: str,
    ) -> None:
        """
        Mark the provider artifact as expired while keeping the internal renewal
        invoice regenerable by clearing stale provider IDs.
        """
        updateData = {
            "status": "expired",
            "shortUrl": None,
            "metadata_json": {
                "artifactExpired": True,
                "artifactType": artifactType,
                "artifactId": artifactId,
                "expiredAt": datetime.datetime.utcnow().isoformat(),
            },
        }
        if artifactType == "razorpay_invoice":
            updateData["razorpayInvoiceId"] = None
        if artifactType == "payment_link":
            updateData["razorpay_payment_link_id"] = None
        self.client.table("Invoices").update(updateData).eq("id", invoiceId).execute()

    @staticmethod
    def _validateFrozenInvoicePaymentMatch(invoice: dict, paymentEntity: dict) -> None:
        """
        Validate payment amount/currency against frozen internal invoice snapshot.

        Raises:
            ValueError: If amount or currency mismatches.
        """
        expectedAmount = invoice.get("total_amount")
        if expectedAmount is None:
            expectedAmount = invoice.get("amount")
        expectedCurrency = invoice.get("currency") or "INR"
        actualAmount = paymentEntity.get("amount")
        actualCurrency = paymentEntity.get("currency", "INR")

        if expectedAmount is not None and int(actualAmount or 0) != int(expectedAmount):
            raise ValueError(
                f"Invoice/payment amount mismatch for invoice {invoice.get('id')}: "
                f"expected={expectedAmount}, actual={actualAmount}"
            )
        if str(actualCurrency).upper() != str(expectedCurrency).upper():
            raise ValueError(
                f"Invoice/payment currency mismatch for invoice {invoice.get('id')}: "
                f"expected={expectedCurrency}, actual={actualCurrency}"
            )

    def _auditLog(self, userId: str, eventType: str, **kwargs) -> None:
        """
        Insert a row into the SubscriptionLog table.

        Args:
            userId (str): The user ID associated with this log entry.
            eventType (str): The event type (e.g. 'subscription.created', 'domain.add_requested').
            **kwargs: Optional fields — status, plus any key-value pairs to store in metadata.
                      Reserved keys: paymentId, invoiceId, amount, currency
                      are auto-mapped into metadata with 'razorpay' prefix where applicable.
        """
        try:
            status = kwargs.pop("status", None)
            existingMeta = kwargs.pop("metadata", None) or {}

            metaFields = {}
            razorpayPrefixed = {"paymentId", "invoiceId"}
            for key in ("paymentId", "invoiceId", "amount", "currency"):
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

    def _handlePaymentAuthorized(self, event: dict) -> None:
        """
        Handle the payment.authorized webhook event.

        Args:
            event (dict): The Razorpay webhook event.
        """
        paymentEntity = event.get("payload", {}).get("payment", {}).get("entity", {})
        notes = paymentEntity.get("notes", {})
        userId = notes.get("userId", "unknown")
        self._auditLog(
            userId, "payment.authorized",
            paymentId=paymentEntity.get("id"),
            amount=paymentEntity.get("amount"),
            currency=paymentEntity.get("currency", "INR"),
            status="AUTHORIZED"
        )
        logger.info(f"Payment authorized: {paymentEntity.get('id')}")

    def _handlePaymentCaptured(self, event: dict) -> None:
        """
        Handle the payment.captured webhook event.

        Branches by payment source via notes.type:
        - recurring_renewal: extends billing cycle by 1 month, resets dunning counter
        - annual_renewal: extends billing cycle by 12 months, marks invoice paid
        - domain_upgrade_proration: activates paid domains (webhook backup for verifyDomainUpgrade)
        - default: log-only

        Args:
            event (dict): The Razorpay webhook event.
        """
        paymentEntity = event.get("payload", {}).get("payment", {}).get("entity", {})
        notes = paymentEntity.get("notes", {})
        paymentType = notes.get("type", "")
        paymentId = paymentEntity.get("id")
        invoiceId = notes.get("invoiceId")

        if paymentType == "recurring_renewal":
            userId = notes.get("userId")
            if not userId:
                logger.error(f"recurring_renewal payment {paymentId} missing userId in notes")
                return
            frozenInvoice = None
            if invoiceId:
                frozenInvoice = self._findInvoiceById(invoiceId)
                if not frozenInvoice:
                    raise Exception(f"Frozen invoice not found for recurring payment: {invoiceId}")
                self._validateFrozenInvoicePaymentMatch(frozenInvoice, paymentEntity)
            user = self._findUserById(userId)
            if not user:
                logger.error(f"No user found for userId {userId} during payment.captured")
                return
            subscription = self._findSubscriptionByUserId(userId)
            if not subscription:
                logger.error(
                    f"No canonical subscription row found for userId {userId} "
                    f"during payment.captured"
                )
                return
            previousExpiry = subscription.get("current_period_end")
            if previousExpiry:
                expiryDt = parser.isoparse(previousExpiry.replace("Z", "+00:00"))
                if expiryDt.tzinfo is not None:
                    expiryDt = expiryDt.replace(tzinfo=None)
            else:
                expiryDt = datetime.datetime.utcnow()
            newExpiry = expiryDt + relativedelta(months=1)
            self.client.table("subscriptions").update({
                "status": "active",
                "current_period_start": expiryDt.isoformat(),
                "current_period_end": newExpiry.isoformat(),
                "renewal_due_at": newExpiry.isoformat(),
            }).eq("id", subscription["id"]).execute()
            self.client.table("Users").update({
                "recurringFailures": 0,
            }).eq("userId", userId).execute()
            if invoiceId:
                self._markInvoicePaid(invoiceId=invoiceId, paymentEntity=paymentEntity)
            else:
                capturedAt = paymentEntity.get("captured_at") or paymentEntity.get("created_at")
                self._upsertInvoiceRecord(userId, {
                    "id": paymentId,
                    "payment_id": paymentId,
                    "amount": paymentEntity.get("amount", 0),
                    "currency": paymentEntity.get("currency", "INR"),
                    "status": "paid",
                    "billing_start": int(expiryDt.timestamp()) if expiryDt else None,
                    "billing_end": int(newExpiry.timestamp()) if newExpiry else None,
                    "paid_at": capturedAt,
                })
            self._auditLog(
                userId, "billing.renewal_charged",
                paymentId=paymentId,
                amount=paymentEntity.get("amount"),
                currency=paymentEntity.get("currency", "INR"),
                status="CHARGED",
                metadata={
                    "invoiceId": invoiceId,
                    "previousExpiry": str(expiryDt),
                    "newExpiry": str(newExpiry),
                }
            )
            logger.info(f"Recurring renewal charged for user {userId}, new expiry {newExpiry}")
        elif paymentType == "domain_upgrade_proration":
            userId = notes.get("userId")
            if not userId:
                logger.error(f"domain_upgrade_proration payment {paymentId} missing userId in notes")
                return
            if invoiceId:
                frozenInvoice = self._findInvoiceById(invoiceId)
                if not frozenInvoice:
                    raise Exception(f"Frozen invoice not found for domain proration payment: {invoiceId}")
                self._validateFrozenInvoicePaymentMatch(frozenInvoice, paymentEntity)
            from api.services.subscriptionService import subscriptionService
            orderId = paymentEntity.get("order_id")
            if orderId:
                subscriptionService._activatePaidDomains(
                    userId=userId,
                    domains=[d.strip() for d in notes.get("domains", "").split(",") if d.strip()],
                    targetQuantity=int(notes.get("targetQuantity", 0)),
                    referenceId=orderId,
                )
            if invoiceId:
                self._markInvoicePaid(invoiceId=invoiceId, paymentEntity=paymentEntity)
            self._auditLog(
                userId, "payment.captured",
                paymentId=paymentId,
                amount=paymentEntity.get("amount"),
                currency=paymentEntity.get("currency", "INR"),
                status="CAPTURED",
                metadata={"type": "domain_upgrade_proration", "orderId": orderId, "invoiceId": invoiceId}
            )
            logger.info(f"Domain upgrade payment captured and activated: {paymentId}")
        elif paymentType == "annual_renewal":
            userId = notes.get("userId")
            if not userId:
                logger.error(f"annual_renewal payment {paymentId} missing userId in notes")
                return
            if not invoiceId:
                logger.error(f"annual_renewal payment {paymentId} missing invoiceId in notes")
                return
            frozenInvoice = self._findInvoiceById(invoiceId)
            if not frozenInvoice:
                raise Exception(f"Frozen invoice not found for annual renewal: {invoiceId}")
            invoiceStatus = (frozenInvoice.get("status") or "").upper()
            if invoiceStatus == "PAID":
                logger.info(f"Invoice {invoiceId} already paid, skipping annual_renewal webhook")
                return
            self._validateFrozenInvoicePaymentMatch(frozenInvoice, paymentEntity)
            user = self._findUserById(userId)
            if not user:
                logger.error(f"No user found for userId {userId} during annual_renewal")
                return
            subscription = self._findSubscriptionByUserId(userId)
            if not subscription:
                logger.error(f"No subscription found for userId {userId} during annual_renewal")
                return
            previousExpiry = subscription.get("current_period_end")
            if previousExpiry:
                expiryDt = parser.isoparse(previousExpiry.replace("Z", "+00:00"))
                if expiryDt.tzinfo is not None:
                    expiryDt = expiryDt.replace(tzinfo=None)
            else:
                expiryDt = datetime.datetime.utcnow()
            newExpiry = expiryDt + relativedelta(years=1)
            self.client.table("subscriptions").update({
                "status": "active",
                "current_period_start": expiryDt.isoformat(),
                "current_period_end": newExpiry.isoformat(),
                "renewal_due_at": newExpiry.isoformat(),
            }).eq("id", subscription["id"]).execute()
            self._markInvoicePaid(invoiceId=invoiceId, paymentEntity=paymentEntity)
            previousStatus = subscription.get("status", "")
            if previousStatus in ("past_due", "suspended"):
                self._sendRenewalNotification(
                    userId=userId, invoiceId=invoiceId,
                    template="subscription_restored",
                    metadata={"previousStatus": previousStatus, "newExpiry": str(newExpiry)},
                )
            self._sendRenewalNotification(
                userId=userId, invoiceId=invoiceId,
                template="payment_success",
                metadata={"amount": paymentEntity.get("amount"), "newExpiry": str(newExpiry)},
            )
            self._auditLog(
                userId, "billing.annual_renewal_charged",
                paymentId=paymentId,
                amount=paymentEntity.get("amount"),
                currency=paymentEntity.get("currency", "INR"),
                status="CHARGED",
                metadata={
                    "invoiceId": invoiceId,
                    "previousExpiry": str(expiryDt),
                    "newExpiry": str(newExpiry),
                    "flow": "webhook_payment_captured",
                    "restoredFrom": previousStatus if previousStatus in ("past_due", "suspended") else None,
                }
            )
            logger.info(f"Annual renewal charged for user {userId}, new expiry {newExpiry}")
        else:
            self._auditLog(
                notes.get("userId", "unknown"), "payment.captured",
                paymentId=paymentId,
                amount=paymentEntity.get("amount"),
                currency=paymentEntity.get("currency", "INR"),
                status="CAPTURED"
            )
            logger.info(f"Payment captured: {paymentId}")

    def _handlePaymentFailed(self, event: dict) -> None:
        """
        Handle the payment.failed webhook event.

        Branches by payment source via notes.type:
        - recurring_renewal: increments dunning counter, expires after 3 failures
        - default: marks pending additions as failed, sends notification email

        Args:
            event (dict): The Razorpay webhook event.
        """
        paymentEntity = event.get("payload", {}).get("payment", {}).get("entity", {})
        notes = paymentEntity.get("notes", {})
        paymentType = notes.get("type", "")
        paymentId = paymentEntity.get("id")
        errorMeta = paymentEntity.get("error_code", "")
        invoiceId = notes.get("invoiceId")

        if paymentType == "recurring_renewal":
            userId = notes.get("userId")
            if not userId:
                logger.error(f"recurring_renewal payment {paymentId} missing userId in notes")
                return
            user = self._findUserById(userId)
            if not user:
                logger.error(f"No user found for userId {userId} during payment.failed")
                return
            failures = (user.get("recurringFailures") or 0) + 1
            updateData = {"recurringFailures": failures}
            if failures >= 3:
                subscription = self._findSubscriptionByUserId(userId)
                if subscription:
                    self.client.table("subscriptions").update({
                        "status": "suspended"
                    }).eq("id", subscription["id"]).execute()
            self.client.table("Users").update(updateData).eq("userId", userId).execute()
            self._auditLog(
                userId, "billing.renewal_failed",
                paymentId=paymentId,
                amount=paymentEntity.get("amount"),
                currency=paymentEntity.get("currency", "INR"),
                status="FAILED",
                metadata={
                    "invoiceId": invoiceId,
                    "error_code": errorMeta,
                    "error_description": paymentEntity.get("error_description", ""),
                    "recurringFailures": failures,
                    "suspended": failures >= 3,
                }
            )
            if invoiceId:
                self._markInvoiceFailed(
                    invoiceId=invoiceId,
                    errorCode=errorMeta,
                    errorDescription=paymentEntity.get("error_description", ""),
                )
            if user.get("email"):
                self._sendPaymentFailureEmail(user["email"], user.get("fullName", ""), "recurring_renewal_failed")
            logger.info(f"Recurring renewal failed for user {userId}, failures={failures}")
        elif paymentType == "annual_renewal":
            userId = notes.get("userId")
            if not userId:
                logger.error(f"annual_renewal payment {paymentId} missing userId in notes")
                return
            self._auditLog(
                userId, "billing.annual_renewal_failed",
                paymentId=paymentId,
                amount=paymentEntity.get("amount"),
                currency=paymentEntity.get("currency", "INR"),
                status="FAILED",
                metadata={
                    "invoiceId": invoiceId,
                    "error_code": errorMeta,
                    "error_description": paymentEntity.get("error_description", ""),
                }
            )
            if invoiceId:
                self._markInvoiceFailed(
                    invoiceId=invoiceId,
                    errorCode=errorMeta,
                    errorDescription=paymentEntity.get("error_description", ""),
                )
                self._sendRenewalNotification(
                    userId=userId, invoiceId=invoiceId,
                    template="payment_failed_retry",
                    metadata={
                        "reason": "payment_failed",
                        "errorCode": errorMeta,
                    },
                )
            logger.info(f"Annual renewal payment failed for user {userId}, payment {paymentId}")
        else:
            userId = notes.get("userId", "unknown")
            self._auditLog(
                userId, "payment.failed",
                paymentId=paymentId,
                amount=paymentEntity.get("amount"),
                currency=paymentEntity.get("currency", "INR"),
                status="FAILED",
                metadata={"invoiceId": invoiceId, "error_code": errorMeta, "error_description": paymentEntity.get("error_description", "")}
            )
            if invoiceId:
                self._markInvoiceFailed(
                    invoiceId=invoiceId,
                    errorCode=errorMeta,
                    errorDescription=paymentEntity.get("error_description", ""),
                )
            user = self._findUserById(userId) if userId != "unknown" else None
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
                self._sendPaymentFailureEmail(user["email"], user["fullName"], "payment_failed")
            logger.info(f"Payment failed: {paymentId}")

    def _handleOrderPaid(self, event: dict) -> None:
        """
        Handle the order.paid webhook event.

        Razorpay can emit both `order.paid` and `payment.captured` for the same
        underlying transaction. To keep finalization consistent and idempotent,
        this handler normalizes the order payload into payment payload shape and
        routes processing through `_handlePaymentCaptured`.

        Args:
            event (dict): Razorpay webhook event payload.
        """
        payload = event.get("payload", {})
        paymentEntity = payload.get("payment", {}).get("entity", {}) or {}
        orderEntity = payload.get("order", {}).get("entity", {}) or {}

        if not paymentEntity:
            logger.warning("order.paid payload missing payment.entity; skipping")
            return

        paymentEntity = dict(paymentEntity)
        notes = paymentEntity.get("notes", {}) or {}
        if not isinstance(notes, dict):
            notes = {}
        if not notes:
            orderNotes = orderEntity.get("notes", {}) or {}
            if isinstance(orderNotes, dict):
                notes = orderNotes
        paymentEntity["notes"] = notes

        if not paymentEntity.get("order_id") and orderEntity.get("id"):
            paymentEntity["order_id"] = orderEntity.get("id")

        normalizedEvent = {
            "payload": {
                "payment": {
                    "entity": paymentEntity
                }
            }
        }
        self._handlePaymentCaptured(normalizedEvent)

    def _handleInvoicePaid(self, event: dict) -> None:
        """
        Handle the invoice.paid webhook event.

        For annual renewal invoices created via the T-7 scheduler's Razorpay
        Invoice artifact, resolves the internal invoice and extends the
        subscription by 12 months. For legacy Razorpay subscription invoices,
        stores the invoice record.

        Args:
            event (dict): The Razorpay webhook event.
        """
        invoiceEntity = event.get("payload", {}).get("invoice", {}).get("entity", {})
        razorpayInvoiceId = invoiceEntity.get("id", "")
        customerId = invoiceEntity.get("customer_id", "")
        notes = invoiceEntity.get("notes", {})
        internalInvoiceId = notes.get("invoiceId")

        if internalInvoiceId:
            internalInvoice = self._findInvoiceById(internalInvoiceId)
            if internalInvoice:
                invoiceStatus = (internalInvoice.get("status") or "").upper()
                if invoiceStatus == "PAID":
                    logger.info(
                        f"Internal invoice {internalInvoiceId} already paid, "
                        f"skipping invoice.paid webhook"
                    )
                    return
                userId = notes.get("userId")
                if not userId:
                    user = self._findUserByCustomerId(customerId) if customerId else None
                    userId = user["userId"] if user else None
                if userId:
                    self._validateFrozenInvoicePaymentMatch(internalInvoice, {
                        "amount": invoiceEntity.get("amount", 0),
                        "currency": invoiceEntity.get("currency", "INR"),
                    })
                    self._markInvoicePaid(invoiceId=internalInvoiceId, paymentEntity={
                        "id": invoiceEntity.get("payment_id"),
                        "amount": invoiceEntity.get("amount", 0),
                        "currency": invoiceEntity.get("currency", "INR"),
                        "captured_at": invoiceEntity.get("paid_at"),
                    })
                    subscription = self._findSubscriptionByUserId(userId)
                    if subscription and subscription.get("billing_mode") == "annual_prepaid":
                        previousExpiry = subscription.get("current_period_end")
                        if previousExpiry:
                            expiryDt = parser.isoparse(previousExpiry.replace("Z", "+00:00"))
                            if expiryDt.tzinfo is not None:
                                expiryDt = expiryDt.replace(tzinfo=None)
                        else:
                            expiryDt = datetime.datetime.utcnow()
                        newExpiry = expiryDt + relativedelta(years=1)
                        self.client.table("subscriptions").update({
                            "status": "active",
                            "current_period_start": expiryDt.isoformat(),
                            "current_period_end": newExpiry.isoformat(),
                            "renewal_due_at": newExpiry.isoformat(),
                        }).eq("id", subscription["id"]).execute()
                        self._auditLog(
                            userId, "billing.annual_renewal_charged",
                            invoiceId=razorpayInvoiceId,
                            paymentId=invoiceEntity.get("payment_id"),
                            amount=invoiceEntity.get("amount"),
                            currency=invoiceEntity.get("currency", "INR"),
                            status="CHARGED",
                            metadata={
                                "internalInvoiceId": internalInvoiceId,
                                "previousExpiry": str(expiryDt),
                                "newExpiry": str(newExpiry),
                                "flow": "webhook_invoice_paid",
                            }
                        )
                        logger.info(
                            f"Annual renewal via invoice.paid for user {userId}, "
                            f"new expiry {newExpiry}"
                        )
                        self._sendRenewalNotification(
                            userId=userId, invoiceId=internalInvoiceId,
                            template="payment_success",
                            metadata={"amount": invoiceEntity.get("amount"), "newExpiry": str(newExpiry)},
                        )
                        return
                    self._auditLog(
                        userId, "invoice.paid",
                        invoiceId=razorpayInvoiceId,
                        paymentId=invoiceEntity.get("payment_id"),
                        amount=invoiceEntity.get("amount"),
                        currency=invoiceEntity.get("currency", "INR"),
                        status="PAID"
                    )
                    logger.info(f"Invoice paid resolved for internal invoice {internalInvoiceId}")
                    return

        user = self._findUserByCustomerId(customerId) if customerId else None
        if not user:
            logger.warning(
                f"No user found for invoice {razorpayInvoiceId} "
                f"(customer_id={customerId})"
            )
            return
        self._upsertInvoiceRecord(userId=user["userId"], invoiceData=invoiceEntity)
        self._auditLog(
            user["userId"], "invoice.paid",
            invoiceId=razorpayInvoiceId,
            paymentId=invoiceEntity.get("payment_id"),
            amount=invoiceEntity.get("amount"),
            currency=invoiceEntity.get("currency", "INR"),
            status="PAID"
        )
        logger.info(f"Invoice paid stored for user {user['userId']}")

    def _handlePaymentLinkPaid(self, event: dict) -> None:
        """
        Handle the payment_link.paid webhook event.

        Resolves internal invoice via the reference_id (internal invoiceId)
        stored in the payment link notes, marks it paid, and extends the
        annual subscription by 12 months.

        Args:
            event (dict): The Razorpay webhook event.
        """
        linkEntity = event.get("payload", {}).get("payment_link", {}).get("entity", {})
        paymentEntity = event.get("payload", {}).get("payment", {}).get("entity", {})
        notes = linkEntity.get("notes", {})
        internalInvoiceId = notes.get("invoiceId") or linkEntity.get("reference_id")
        userId = notes.get("userId")
        paymentId = paymentEntity.get("id") or linkEntity.get("payment_id")

        if not internalInvoiceId:
            logger.warning(f"payment_link.paid missing invoiceId in notes/reference_id")
            return

        internalInvoice = self._findInvoiceById(internalInvoiceId)
        if not internalInvoice:
            logger.warning(f"Internal invoice {internalInvoiceId} not found for payment_link.paid")
            return

        invoiceStatus = (internalInvoice.get("status") or "").upper()
        if invoiceStatus == "PAID":
            logger.info(f"Invoice {internalInvoiceId} already paid, skipping payment_link.paid")
            return

        if not userId:
            userId = internalInvoice.get("userId")

        self._validateFrozenInvoicePaymentMatch(internalInvoice, {
            "amount": linkEntity.get("amount", 0),
            "currency": linkEntity.get("currency", "INR"),
        })

        self._markInvoicePaid(invoiceId=internalInvoiceId, paymentEntity={
            "id": paymentId,
            "amount": linkEntity.get("amount", 0),
            "currency": linkEntity.get("currency", "INR"),
            "captured_at": paymentEntity.get("captured_at") or paymentEntity.get("created_at"),
        })

        if userId:
            subscription = self._findSubscriptionByUserId(userId)
            if subscription and subscription.get("billing_mode") == "annual_prepaid":
                previousExpiry = subscription.get("current_period_end")
                if previousExpiry:
                    expiryDt = parser.isoparse(previousExpiry.replace("Z", "+00:00"))
                    if expiryDt.tzinfo is not None:
                        expiryDt = expiryDt.replace(tzinfo=None)
                else:
                    expiryDt = datetime.datetime.utcnow()
                newExpiry = expiryDt + relativedelta(years=1)
                self.client.table("subscriptions").update({
                    "status": "active",
                    "current_period_start": expiryDt.isoformat(),
                    "current_period_end": newExpiry.isoformat(),
                    "renewal_due_at": newExpiry.isoformat(),
                }).eq("id", subscription["id"]).execute()
                self._auditLog(
                    userId, "billing.annual_renewal_charged",
                    paymentId=paymentId,
                    amount=linkEntity.get("amount"),
                    currency=linkEntity.get("currency", "INR"),
                    status="CHARGED",
                    metadata={
                        "internalInvoiceId": internalInvoiceId,
                        "previousExpiry": str(expiryDt),
                        "newExpiry": str(newExpiry),
                        "flow": "webhook_payment_link_paid",
                    }
                )
                logger.info(
                    f"Annual renewal via payment_link.paid for user {userId}, "
                    f"new expiry {newExpiry}"
                )
                self._sendRenewalNotification(
                    userId=userId, invoiceId=internalInvoiceId,
                    template="payment_success",
                    metadata={"amount": linkEntity.get("amount"), "newExpiry": str(newExpiry)},
                )
                return

        self._auditLog(
            userId or "unknown", "payment_link.paid",
            paymentId=paymentId,
            amount=linkEntity.get("amount"),
            currency=linkEntity.get("currency", "INR"),
            status="PAID"
        )
        logger.info(f"Payment link paid for invoice {internalInvoiceId}")

    def _handleTokenConfirmed(self, event: dict) -> None:
        """
        Handle the token.confirmed webhook event.

        Logs that the saved mandate (token) was confirmed by Razorpay.

        Args:
            event (dict): The Razorpay webhook event.
        """
        tokenEntity = event.get("payload", {}).get("token", {}).get("entity", {})
        customerId = tokenEntity.get("customer_id", "")
        user = self._findUserByCustomerId(customerId) if customerId else None
        userId = user["userId"] if user else "unknown"
        self._auditLog(
            userId, "token.confirmed",
            status="CONFIRMED",
            metadata={
                "tokenId": tokenEntity.get("id"),
                "customerId": customerId,
                "maxAmount": tokenEntity.get("max_amount"),
            }
        )
        logger.info(f"Token confirmed for customer {customerId}")

    def _handleTokenCancelled(self, event: dict) -> None:
        """
        Handle the token.cancelled webhook event.

        Clears the user's saved token to prevent future recurring charges.

        Args:
            event (dict): The Razorpay webhook event.
        """
        tokenEntity = event.get("payload", {}).get("token", {}).get("entity", {})
        customerId = tokenEntity.get("customer_id", "")
        user = self._findUserByCustomerId(customerId) if customerId else None
        if user:
            self.client.table("Users").update({
                "razorpayTokenId": None,
            }).eq("userId", user["userId"]).execute()
            self._auditLog(
                user["userId"], "token.cancelled",
                status="CANCELLED",
                metadata={"tokenId": tokenEntity.get("id"), "customerId": customerId}
            )
            logger.info(f"Token cancelled for user {user['userId']}, cleared razorpayTokenId")
        else:
            logger.warning(f"Token cancelled for unknown customer {customerId}")

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

    def _sendRenewalNotification(
        self, userId: str, invoiceId: str, template: str,
        metadata: dict | None = None,
    ) -> None:
        """
        Send a renewal lifecycle notification email with template version
        tracking, send-log deduplication, and delivery status logging.

        Uses SubscriptionLog as the send-log with a dedupe key of
        (invoiceId, template) to enforce exactly-once delivery per
        template per invoice.

        Args:
            userId: Internal user ID.
            invoiceId: Internal invoice primary key.
            template: Template identifier (e.g. payment_success, suspension_notice).
            metadata: Optional metadata to include in the email payload.
        """
        sendLogKey = f"{invoiceId}:{template}"
        existingLog = (
            self.client.table("SubscriptionLog")
            .select("id, metadata")
            .eq("userId", userId)
            .eq("eventType", f"email.{template}")
            .eq("status", sendLogKey)
            .execute()
            .data
        )
        maxAttempts = 1 if template in RENEWAL_EMAIL_SINGLE_SEND_TEMPLATES else RENEWAL_EMAIL_MAX_SEND_ATTEMPTS
        if not self._shouldSendRenewalEmail(existingLog, maxAttempts=maxAttempts):
            return

        user = self._findUserById(userId)
        if not user:
            logger.warning(f"Cannot send {template} email: user {userId} not found")
            return

        emailUrl = os.environ.get("RENEWAL_REMINDER_EMAIL_URL")
        if not emailUrl:
            logger.info(f"{template} email skipped: RENEWAL_REMINDER_EMAIL_URL not configured")
            return

        payload = {
            "email": user.get("email", ""),
            "name": user.get("fullName", ""),
            "template": template,
            "templateVersion": "1",
            "invoiceId": invoiceId,
        }
        if metadata:
            payload.update(metadata)

        deliveryStatus = "SENT"
        try:
            response = requests.post(
                url=emailUrl,
                data=json.dumps(payload),
                headers={"Authorization": f"Bearer {os.environ.get('SUPABASE_KEY', '')}"},
                timeout=10,
            )
            if response.status_code >= 400:
                deliveryStatus = "DELIVERY_FAILED"
                logger.warning(
                    f"{template} email delivery failed for user {userId}, "
                    f"status={response.status_code}"
                )
        except Exception as e:
            deliveryStatus = "DELIVERY_FAILED"
            logger.error(f"{template} email send failed for user {userId}: {e}")

        self.client.table("SubscriptionLog").insert({
            "userId": userId,
            "eventType": f"email.{template}",
            "status": sendLogKey,
            "metadata": {
                "invoiceId": invoiceId,
                "template": template,
                "templateVersion": "1",
                "deliveryStatus": deliveryStatus,
                "sentAt": datetime.datetime.utcnow().isoformat(),
            },
        }).execute()
        logger.info(f"{template} email dispatched for user {userId}, delivery={deliveryStatus}")

    @staticmethod
    def _shouldSendRenewalEmail(existingLogs: list[dict] | None, maxAttempts: int) -> bool:
        logs = existingLogs or []
        if not logs:
            return True
        for log in logs:
            metadata = log.get("metadata") or {}
            if metadata.get("deliveryStatus") == "SENT":
                return False
        return len(logs) < maxAttempts

    def _handleInvoiceExpired(self, event: dict) -> None:
        """
        Handle the invoice.expired webhook event.

        Marks the internal invoice as expired and triggers a
        payment-failed reminder if the invoice was for annual renewal.

        Args:
            event (dict): The Razorpay webhook event.
        """
        invoiceEntity = event.get("payload", {}).get("invoice", {}).get("entity", {})
        notes = invoiceEntity.get("notes", {})
        internalInvoiceId = notes.get("invoiceId")
        userId = notes.get("userId")
        razorpayInvoiceId = invoiceEntity.get("id", "")

        if not internalInvoiceId:
            logger.info(f"invoice.expired for non-internal invoice {razorpayInvoiceId}, skipping")
            return

        internalInvoice = self._findInvoiceById(internalInvoiceId)
        if not internalInvoice:
            logger.warning(f"Internal invoice {internalInvoiceId} not found for invoice.expired")
            return

        invoiceStatus = (internalInvoice.get("status") or "").upper()
        if invoiceStatus in ("PAID", "VOID"):
            logger.info(
                f"Invoice {internalInvoiceId} already resolved ({invoiceStatus}), "
                f"skipping invoice.expired"
            )
            return

        self._markHostedArtifactExpired(
            invoiceId=internalInvoiceId,
            artifactType="razorpay_invoice",
            artifactId=razorpayInvoiceId,
        )

        if not userId:
            userId = internalInvoice.get("userId")

        if userId:
            self._sendRenewalNotification(
                userId=userId, invoiceId=internalInvoiceId,
                template="payment_failed_retry",
                metadata={"reason": "invoice_expired", "razorpayInvoiceId": razorpayInvoiceId},
            )

        self._auditLog(
            userId or "unknown", "invoice.expired",
            invoiceId=razorpayInvoiceId,
            status="EXPIRED",
            metadata={"internalInvoiceId": internalInvoiceId}
        )
        logger.info(f"Invoice expired: {razorpayInvoiceId} (internal: {internalInvoiceId})")

    def _handlePaymentLinkExpired(self, event: dict) -> None:
        """
        Handle the payment_link.expired webhook event.

        Marks metadata on the internal invoice and triggers a
        payment-failed reminder for annual renewal invoices.

        Args:
            event (dict): The Razorpay webhook event.
        """
        linkEntity = event.get("payload", {}).get("payment_link", {}).get("entity", {})
        notes = linkEntity.get("notes", {})
        internalInvoiceId = notes.get("invoiceId") or linkEntity.get("reference_id")
        userId = notes.get("userId")
        linkId = linkEntity.get("id", "")

        if not internalInvoiceId:
            logger.info(f"payment_link.expired for non-internal link {linkId}, skipping")
            return

        internalInvoice = self._findInvoiceById(internalInvoiceId)
        if not internalInvoice:
            logger.warning(
                f"Internal invoice {internalInvoiceId} not found for payment_link.expired"
            )
            return

        invoiceStatus = (internalInvoice.get("status") or "").upper()
        if invoiceStatus in ("PAID", "VOID"):
            logger.info(
                f"Invoice {internalInvoiceId} already resolved ({invoiceStatus}), "
                f"skipping payment_link.expired"
            )
            return

        self._markHostedArtifactExpired(
            invoiceId=internalInvoiceId,
            artifactType="payment_link",
            artifactId=linkId,
        )

        if not userId:
            userId = internalInvoice.get("userId")

        if userId:
            self._sendRenewalNotification(
                userId=userId, invoiceId=internalInvoiceId,
                template="payment_failed_retry",
                metadata={"reason": "payment_link_expired", "paymentLinkId": linkId},
            )

        self._auditLog(
            userId or "unknown", "payment_link.expired",
            status="EXPIRED",
            metadata={"internalInvoiceId": internalInvoiceId, "paymentLinkId": linkId}
        )
        logger.info(f"Payment link expired: {linkId} (internal: {internalInvoiceId})")


webhookService = WebhookService()
