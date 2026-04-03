"""
subscriptionService.py

This module provides the SubscriptionService class, which encapsulates
all business logic related to user subscriptions, including free trials,
Razorpay subscription checkout verification, customer management,
and payment audit logging.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["subscriptionService"]


from utils.exceptionHandler import CustomException
from utils.logger import logger
from api.commons import client
from jose import jwt
import requests
import razorpay
import datetime
import hashlib
import hmac
import json
import os


class SubscriptionService:
    """
    Service class for user subscription management.

    Handles free trials, Razorpay subscription creation,
    and checkout signature verification.
    """

    def __init__(self) -> None:
        """
        Initialize the SubscriptionService.
        """
        logger.info("Initializing Subscription Service.")
        self.client = client
        self.razorpayClient = razorpay.Client(
            auth=(
                os.environ.get("RAZORPAY_KEY_ID", ""),
                os.environ.get("RAZORPAY_KEY_SECRET", "")
            )
        )
        self.BASE_PLAN_ID = os.environ.get("RAZORPAY_PRO_PLAN_ID", "")
        self.VALID_DOMAINS = {"banking", "manufacturing", "supplychain", "telecom"}

    def _auditLog(self, userId: str, action: str, **kwargs) -> None:
        """
        Insert a row into the PaymentAuditLog table.

        Args:
            userId (str): The user ID associated with this audit entry.
            action (str): The action being logged (e.g. 'subscription.created').
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

    def _getOrCreateRazorpayCustomer(self, userId: str, email: str, name: str) -> str:
        """
        Retrieve existing or create a new Razorpay Customer for the user.

        Args:
            userId (str): The internal user ID.
            email (str): The user's email address.
            name (str): The user's full name.

        Returns:
            str: The Razorpay Customer ID.
        """
        record = self.client.table("Users").select("razorpayCustomerId").eq("userId", userId).execute()
        existingCustomerId = record.data[0].get("razorpayCustomerId") if record.data else None
        if existingCustomerId:
            return existingCustomerId
        customer = self.razorpayClient.customer.create({
            "name": name,
            "email": email,
            "fail_existing": 0
        })
        customerId = customer["id"]
        self.client.table("Users").update({
            "razorpayCustomerId": customerId
        }).eq("userId", userId).execute()
        logger.info(f"Razorpay customer created for user {userId}: {customerId}")
        return customerId

    def _normalizeAndValidateDomains(self, domains: list[str]) -> list[str]:
        """
        Normalize and validate domain input for subscription workflows.

        Normalization rules:
        - Trim surrounding whitespace
        - Lowercase domain names

        Validation rules:
        - Must be a non-empty list of strings
        - Domain count must be between 1 and 4
        - No duplicate domains allowed
        - All domains must be in VALID_DOMAINS

        Args:
            domains (list[str]): Raw domains from request payload.

        Returns:
            list[str]: Normalized domain list preserving input order.
        """
        if not isinstance(domains, list):
            raise Exception("Domains must be provided as a list")
        normalizedDomains = []
        for domain in domains:
            if not isinstance(domain, str):
                raise Exception("All domains must be strings")
            normalized = domain.strip().lower()
            if not normalized:
                raise Exception("Domain values must be non-empty strings")
            normalizedDomains.append(normalized)
        if not normalizedDomains or len(normalizedDomains) > 4:
            raise Exception("Domain count must be between 1 and 4")
        if len(set(normalizedDomains)) != len(normalizedDomains):
            raise Exception("Duplicate domains not allowed. Domains must be unique.")
        invalidDomains = set(normalizedDomains) - self.VALID_DOMAINS
        if invalidDomains:
            raise Exception(f"Invalid domains: {', '.join(invalidDomains)}")
        return normalizedDomains

    def _normalizeSingleDomain(self, domain: str) -> str:
        """
        Normalize and validate a single domain value.

        Args:
            domain (str): Raw domain input.

        Returns:
            str: Normalized domain value.
        """
        return self._normalizeAndValidateDomains([domain])[0]

    @staticmethod
    def _sendFreeTrialEmail(email: str, name: str) -> None:
        """
        Send free trial email to a user.

        Args:
            email (str): The email address of the user.
            name (str): The name of the user.
        """
        try:
            requests.post(
                url=os.environ["FREE_TRIAL_EMAIL_URL"],
                data=json.dumps({
                    "email": email,
                    "name": name
                }),
                headers={
                    "Authorization": f"Bearer {os.environ['SUPABASE_KEY']}"
                }
            )
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def activateFreeTrial(self, token: str) -> dict:
        """
        Activate a free trial for a user.

        Args:
            token (str): Authorization token.

        Returns:
            dict: The affected subscription fields.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            userEmail = decodedToken.get("email")
            currentTime = datetime.datetime.utcnow()
            trialDurationDays = 12
            updateData = {
                "subscriptionPlan": "free",
                "subscriptionStatus": "TRIAL",
                "subscriptionStart": str(currentTime),
                "subscriptionExpiry": str(
                    currentTime + datetime.timedelta(days=trialDurationDays)
                ),
                "subscribedExperts": "banking, manufacturing, supplychain, telecom"
            }
            records = self.client.table("Users").update(updateData).eq("userId", userId).execute()
            name = records.data[0]["fullName"]
            self._sendFreeTrialEmail(email=userEmail, name=name)
            self._auditLog(userId, "free_trial.activated", status="TRIAL")
            return updateData
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def createSubscription(self, domains: list[str], token: str) -> dict:
        """
        Create a Razorpay subscription for the given domains using quantity billing.

        Args:
            domains (list[str]): Domain expert names the user is subscribing to.
            token (str): Authorization token.

        Returns:
            dict: Data required to open Razorpay checkout.
        """
        try:
            normalizedDomains = self._normalizeAndValidateDomains(domains)
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            userEmail = decodedToken.get("email")
            userRecord = self.client.table("Users").select("fullName").eq("userId", userId).execute()
            fullName = userRecord.data[0]["fullName"] if userRecord.data else userEmail
            customerId = self._getOrCreateRazorpayCustomer(userId, userEmail, fullName)
            quantity = len(normalizedDomains)
            subscription = self.razorpayClient.subscription.create({
                "plan_id": self.BASE_PLAN_ID,
                "customer_notify": 1,
                "total_count": 12,
                "quantity": quantity,
                "customer_id": customerId
            })
            self._auditLog(
                userId, "subscription.created",
                subscriptionId=subscription["id"],
                status=subscription["status"],
                metadata={"domains": normalizedDomains, "quantity": quantity}
            )
            return {
                "userId": userId,
                "userEmail": userEmail,
                "razorpayKey": os.environ["RAZORPAY_KEY_ID"],
                "subscriptionId": subscription["id"],
                "shortUrl": subscription["short_url"],
                "status": subscription["status"],
                "quantity": quantity,
                "domains": normalizedDomains
            }
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def verifySubscription(self, payload: dict, token: str) -> None:
        """
        Verify Razorpay subscription checkout signature.

        This method performs official HMAC SHA256 verification
        using Razorpay API secret. On success, stores the domain
        count and sets the subscription plan to 'pro'.

        Args:
            payload (dict): Razorpay checkout response payload.
            token (str): Authorization token.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            paymentId = payload.get("razorpayPaymentId")
            subscriptionId = payload.get("razorpaySubscriptionId")
            signature = payload.get("razorpaySignature")
            domains = payload.get("domains")
            if not all([paymentId, subscriptionId, signature, userId, domains]):
                raise Exception("Missing Razorpay verification fields")
            normalizedDomains = self._normalizeAndValidateDomains(domains)
            message = f"{paymentId}|{subscriptionId}"
            expectedSignature = hmac.new(
                os.environ["RAZORPAY_KEY_SECRET"].encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expectedSignature, signature):
                raise Exception("Invalid Razorpay signature")
            currentTime = datetime.datetime.utcnow()
            expiry = currentTime + datetime.timedelta(days=30)
            self.client.table("Users").update({
                "razorpaySubscriptionId": subscriptionId,
                "subscriptionPlan": "pro",
                "subscriptionStatus": "ACTIVE",
                "subscriptionStart": str(currentTime),
                "subscriptionExpiry": str(expiry),
                "subscribedExperts": ", ".join(normalizedDomains),
                "domainCount": len(normalizedDomains),
                "pendingRemovals": []
            }).eq("userId", userId).execute()
            self._auditLog(
                userId, "subscription.verified",
                paymentId=paymentId,
                subscriptionId=subscriptionId,
                status="ACTIVE",
                metadata={"domains": normalizedDomains, "quantity": len(normalizedDomains)}
            )
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def addDomain(self, domain: str, token: str) -> dict:
        """
        Initiate adding a domain to an active subscription.

        Calls Razorpay to increase the subscription quantity but does NOT
        grant immediate access. The domain is placed in pendingAdditions
        and activated only after webhook reconciliation confirms payment.

        Args:
            domain (str): The domain expert name to add.
            token (str): Authorization token.

        Returns:
            dict: Initiation state with upgradeState and checkoutUrl.
        """
        try:
            normalizedDomain = self._normalizeSingleDomain(domain)
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            userRecord = self.client.table("Users") \
                .select("subscribedExperts, domainCount, razorpaySubscriptionId, subscriptionStatus, pendingRemovals, pendingAdditions") \
                .eq("userId", userId) \
                .execute()
            if not userRecord.data:
                raise Exception("User not found")
            user = userRecord.data[0]
            if user["subscriptionStatus"] != "ACTIVE":
                raise Exception("Subscription must be active to add a domain")
            currentExperts = [e.strip() for e in user["subscribedExperts"].split(",") if e.strip()]
            if normalizedDomain in currentExperts:
                raise Exception(f"Domain '{normalizedDomain}' is already in your subscription")
            pendingAdditions = user.get("pendingAdditions") or []
            pendingDomains = [item["domain"] for item in pendingAdditions if item.get("state") not in ("failed", "activated")]
            if normalizedDomain in pendingDomains:
                raise Exception(f"Domain '{normalizedDomain}' already has a pending addition request")
            domainCount = user["domainCount"] or len(currentExperts)
            totalWithPending = domainCount + len(pendingDomains)
            if totalWithPending >= 4:
                raise Exception("Maximum of 4 domains reached (including pending additions)")
            subscriptionId = user["razorpaySubscriptionId"]
            if not subscriptionId:
                raise Exception("No active subscription found for this user")
            newQuantity = domainCount + len(pendingDomains) + 1
            effectiveAt = "now"
            upgradeState = "processing"
            try:
                self.razorpayClient.subscription.edit(subscriptionId, {
                    "quantity": newQuantity,
                    "schedule_change_at": "now"
                })
            except Exception as razorpayError:
                if "minimum amount" in str(razorpayError).lower():
                    self.razorpayClient.subscription.edit(subscriptionId, {
                        "quantity": newQuantity,
                        "schedule_change_at": "cycle_end"
                    })
                    effectiveAt = "cycle_end"
                    upgradeState = "scheduled_cycle_end"
                    logger.info(f"Prorated amount below minimum for user {userId}, falling back to cycle_end")
                else:
                    raise razorpayError
            pendingItem = {
                "domain": normalizedDomain,
                "currentQuantity": domainCount,
                "targetQuantity": newQuantity,
                "state": upgradeState,
                "checkoutUrl": None,
                "requestedAt": str(datetime.datetime.utcnow()),
                "invoiceId": None,
                "paymentId": None,
            }
            pendingAdditions.append(pendingItem)
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", userId).execute()
            pendingRemovals = user.get("pendingRemovals") or []
            if pendingRemovals and effectiveAt == "now":
                scheduledQuantity = newQuantity - len(pendingRemovals)
                self.razorpayClient.subscription.edit(subscriptionId, {
                    "quantity": scheduledQuantity,
                    "schedule_change_at": "cycle_end"
                })
                logger.info(f"Re-scheduled pending removals for user {userId}: qty {scheduledQuantity} at cycle_end")
            self.client.table("DomainChangeLog").insert({
                "userId": userId,
                "action": "add_requested",
                "domain": normalizedDomain,
                "previousQuantity": domainCount,
                "newQuantity": newQuantity,
                "proratedAmount": 0,
                "effectiveAt": effectiveAt
            }).execute()
            self._auditLog(
                userId, "domain.add_requested",
                subscriptionId=subscriptionId,
                status=upgradeState,
                metadata={
                    "domain": normalizedDomain,
                    "previousQuantity": domainCount,
                    "newQuantity": newQuantity,
                    "effectiveAt": effectiveAt
                }
            )
            logger.info(f"Domain '{normalizedDomain}' add requested for user {userId}, quantity {domainCount} -> {newQuantity}")
            return {
                "domain": normalizedDomain,
                "currentQuantity": domainCount,
                "targetQuantity": newQuantity,
                "effectiveAt": effectiveAt,
                "upgradeState": upgradeState,
                "checkoutUrl": None,
                "pendingRemovals": pendingRemovals
            }
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def removeDomain(self, domains: list[str], token: str) -> dict:
        """
        Schedule one or more domain removals at the end of the current billing cycle.

        The user retains access to all removed domains until cycle end. Razorpay is
        told to reduce quantity at cycle_end so no refund is issued. At least one
        domain must remain active after the operation.

        Args:
            domains (list[str]): The domain expert names to remove.
            token (str): Authorization token.

        Returns:
            dict: Current domains, pending removals, and effective timing.
        """
        try:
            normalizedDomains = self._normalizeAndValidateDomains(domains)
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            userRecord = self.client.table("Users") \
                .select("subscribedExperts, domainCount, razorpaySubscriptionId, subscriptionStatus, pendingRemovals") \
                .eq("userId", userId) \
                .execute()
            if not userRecord.data:
                raise Exception("User not found")
            user = userRecord.data[0]
            if user["subscriptionStatus"] != "ACTIVE":
                raise Exception("Subscription must be active to remove a domain")
            currentExperts = [e.strip() for e in user["subscribedExperts"].split(",") if e.strip()]
            pendingRemovals = user.get("pendingRemovals") or []
            for domain in normalizedDomains:
                if domain not in currentExperts:
                    raise Exception(f"Domain '{domain}' is not in your active domains")
                if domain in pendingRemovals:
                    raise Exception(f"Domain '{domain}' is already scheduled for removal")
            activeDomains = [d for d in currentExperts if d not in pendingRemovals]
            if len(activeDomains) - len(normalizedDomains) < 1:
                raise Exception("Cannot remove all domains. At least one must remain active. Use cancel subscription instead.")
            subscriptionId = user["razorpaySubscriptionId"]
            if not subscriptionId:
                raise Exception("No active subscription found for this user")
            domainCount = user["domainCount"] or len(currentExperts)
            activeCount = domainCount - len(pendingRemovals)
            newQuantity = activeCount - len(normalizedDomains)
            if newQuantity < 1:
                raise Exception("Cannot remove all domains. At least one must remain active. Use cancel subscription instead.")
            self.razorpayClient.subscription.edit(subscriptionId, {
                "quantity": newQuantity,
                "schedule_change_at": "cycle_end"
            })
            pendingRemovals.extend(normalizedDomains)
            self.client.table("Users").update({
                "pendingRemovals": pendingRemovals
            }).eq("userId", userId).execute()
            for domain in normalizedDomains:
                self.client.table("DomainChangeLog").insert({
                    "userId": userId,
                    "action": "remove",
                    "domain": domain,
                    "previousQuantity": domainCount,
                    "newQuantity": newQuantity,
                    "proratedAmount": 0,
                    "effectiveAt": "cycle_end"
                }).execute()
            self._auditLog(
                userId, "domain.removal_scheduled",
                subscriptionId=subscriptionId,
                status="ACTIVE",
                metadata={
                    "domains": normalizedDomains,
                    "previousQuantity": domainCount,
                    "newQuantity": newQuantity,
                    "effectiveAt": "cycle_end"
                }
            )
            logger.info(f"Domains {normalizedDomains} scheduled for removal at cycle_end for user {userId}")
            return {
                "currentDomains": currentExperts,
                "pendingRemovals": pendingRemovals,
                "effectiveAt": "cycle_end"
            }
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception




    def cancelPendingAddition(self, domain: str, token: str) -> dict:
        """
        Cancel a pending domain addition request.

        Removes the domain from pendingAdditions and reverts the Razorpay
        subscription quantity if the addition was still in progress.

        Args:
            domain (str): The domain to cancel.
            token (str): Authorization token.

        Returns:
            dict: Confirmation of cancellation.
        """
        try:
            normalizedDomain = self._normalizeSingleDomain(domain)
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            userRecord = self.client.table("Users") \
                .select("pendingAdditions, domainCount, razorpaySubscriptionId, pendingRemovals") \
                .eq("userId", userId) \
                .execute()
            if not userRecord.data:
                raise Exception("User not found")
            user = userRecord.data[0]
            pendingAdditions = user.get("pendingAdditions") or []
            targetItem = None
            targetIndex = None
            for i, item in enumerate(pendingAdditions):
                if item["domain"] == normalizedDomain and item.get("state") not in ("activated",):
                    targetItem = item
                    targetIndex = i
                    break
            if targetItem is None:
                raise Exception(f"No cancellable pending addition found for domain '{normalizedDomain}'")
            shouldRevertQuantity = targetItem.get("state") in ("processing", "awaiting_payment", "scheduled_cycle_end")
            pendingAdditions.pop(targetIndex)
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", userId).execute()
            if shouldRevertQuantity:
                subscriptionId = user["razorpaySubscriptionId"]
                if subscriptionId:
                    domainCount = user["domainCount"] or 0
                    activePendingCount = len([
                        item for item in pendingAdditions
                        if item.get("state") not in ("failed", "activated")
                    ])
                    revertQuantity = domainCount + activePendingCount
                    pendingRemovals = user.get("pendingRemovals") or []
                    if pendingRemovals:
                        revertQuantity = revertQuantity - len(pendingRemovals)
                    try:
                        self.razorpayClient.subscription.edit(subscriptionId, {
                            "quantity": max(revertQuantity, 1),
                            "schedule_change_at": "now"
                        })
                    except Exception as rzpError:
                        logger.error(f"Failed to revert Razorpay quantity for user {userId}: {rzpError}")
            self.client.table("DomainChangeLog").insert({
                "userId": userId,
                "action": "add_cancelled",
                "domain": normalizedDomain,
                "previousQuantity": targetItem.get("targetQuantity", 0),
                "newQuantity": (user["domainCount"] or 0),
                "proratedAmount": 0,
                "effectiveAt": "immediate"
            }).execute()
            self._auditLog(
                userId, "domain.add_cancelled",
                subscriptionId=user.get("razorpaySubscriptionId"),
                status="CANCELLED",
                metadata={"domain": normalizedDomain}
            )
            logger.info(f"Pending addition cancelled for domain '{normalizedDomain}', user {userId}")
            return {"domain": normalizedDomain, "cancelled": True}
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def cancelSubscription(self, token: str) -> dict:
        """
        Cancel a user's Razorpay subscription at the end of the current billing cycle.

        The user retains full access until the cycle ends. The actual status
        transition to 'cancelled' is handled by the subscription.cancelled webhook.

        Args:
            token (str): Authorization token.

        Returns:
            dict: The Razorpay cancellation response.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            userRecord = self.client.table("Users") \
                .select("razorpaySubscriptionId") \
                .eq("userId", userId) \
                .execute()
            subscriptionId = userRecord.data[0].get("razorpaySubscriptionId") if userRecord.data else None
            if not subscriptionId:
                raise Exception("No active subscription found for this user")
            result = self.razorpayClient.subscription.cancel(
                subscriptionId,
                {"cancel_at_cycle_end": 1}
            )
            self.client.table("Users").update({
                "subscriptionStatus": "PENDING_CANCELLATION",
            }).eq("userId", userId).execute()
            self._auditLog(
                userId, "subscription.cancellation_scheduled",
                subscriptionId=subscriptionId,
                status="PENDING_CANCELLATION",
                metadata={"cancel_at_cycle_end": True}
            )
            logger.info(f"Subscription {subscriptionId} scheduled for cancellation at cycle end for user {userId}")
            return result
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def initiateRefund(self, token: str, paymentId: str, amount: int | None = None) -> dict:
        """
        Initiate a refund for a Razorpay payment.

        Args:
            token (str): Authorization token.
            paymentId (str): The Razorpay payment ID to refund.
            amount (int | None): Amount in paise to refund. None for full refund.

        Returns:
            dict: The Razorpay refund response.
        """
        try:
            if not paymentId:
                raise Exception("paymentId is required")
            if amount is not None and amount <= 0:
                raise Exception("amount must be greater than 0")
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            if not userId:
                raise Exception("Invalid token payload")
            userRecord = self.client.table("Users") \
                .select("razorpayCustomerId, razorpaySubscriptionId") \
                .eq("userId", userId) \
                .execute()
            if not userRecord.data:
                raise Exception("User not found")
            customerId = userRecord.data[0].get("razorpayCustomerId")
            subscriptionId = userRecord.data[0].get("razorpaySubscriptionId")
            if not customerId:
                raise Exception("No Razorpay customer found for this user")
            payment = self.razorpayClient.payment.fetch(paymentId)
            paymentCustomerId = payment.get("customer_id")
            if not paymentCustomerId or paymentCustomerId != customerId:
                logger.warning(f"Unauthorized refund attempt blocked for user {userId}, payment {paymentId}")
                raise Exception("Payment does not belong to this user")
            if subscriptionId and payment.get("subscription_id") != subscriptionId:
                logger.warning(f"Subscription ownership mismatch on refund attempt for user {userId}, payment {paymentId}")
                raise Exception("Payment does not belong to this user's subscription")
            refundParams = {}
            if amount is not None:
                refundParams["amount"] = amount
            result = self.razorpayClient.payment.refund(paymentId, refundParams)
            self._auditLog(
                userId, "refund.initiated",
                paymentId=paymentId,
                subscriptionId=subscriptionId,
                amount=amount or payment.get("amount"),
                currency=payment.get("currency", "INR"),
                status="initiated"
            )
            logger.info(f"Refund initiated for payment {paymentId}, user {userId}")
            return result
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def storeInvoice(self, userId: str, invoiceData: dict) -> None:
        """
        Store or update an invoice record in the Invoices table.

        Args:
            userId (str): The user ID to associate the invoice with.
            invoiceData (dict): Razorpay invoice entity data.
        """
        try:
            billingStart = invoiceData.get("billing_start")
            billingEnd = invoiceData.get("billing_end")
            paidAt = invoiceData.get("paid_at")
            record = {
                "userId": userId,
                "razorpayInvoiceId": invoiceData["id"],
                "razorpaySubscriptionId": invoiceData.get("subscription_id"),
                "razorpayPaymentId": invoiceData.get("payment_id"),
                "amount": invoiceData.get("amount", 0),
                "currency": invoiceData.get("currency", "INR"),
                "status": invoiceData.get("status", "paid"),
                "billingStart": str(datetime.datetime.utcfromtimestamp(billingStart)) if billingStart else None,
                "billingEnd": str(datetime.datetime.utcfromtimestamp(billingEnd)) if billingEnd else None,
                "paidAt": str(datetime.datetime.utcfromtimestamp(paidAt)) if paidAt else None,
                "shortUrl": invoiceData.get("short_url"),
            }
            self.client.table("Invoices").upsert(
                record, on_conflict="razorpayInvoiceId"
            ).execute()
        except Exception as e:
            logger.error(f"Failed to store invoice {invoiceData.get('id')} for user {userId}: {e}")

    def getInvoices(self, token: str) -> list[dict]:
        """
        Retrieve all invoices for the authenticated user.

        Args:
            token (str): Authorization token.

        Returns:
            list[dict]: List of invoice records ordered by creation date descending.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms = ["HS256"]
            )
            userId = decodedToken.get("userId")
            result = self.client.table("Invoices") \
                .select("*") \
                .eq("userId", userId) \
                .order("createdAt", desc=True) \
                .execute()
            return result.data
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception


subscriptionService = SubscriptionService()
