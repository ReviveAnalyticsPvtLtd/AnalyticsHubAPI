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

    def _reconcilePendingAdditions(self, user: dict) -> dict:
        """
        Reconcile awaiting_payment entries against Razorpay Payment Link status.

        Activates paid domains, removes expired link entries. Called at the start
        of addDomains() and removeDomain() to resolve any missed activations.

        Args:
            user (dict): The user record with pendingAdditions.

        Returns:
            dict: The (possibly refreshed) user record.
        """
        pendingAdditions = user.get("pendingAdditions") or []
        changed = False
        for item in pendingAdditions:
            if item.get("state") != "awaiting_payment":
                continue
            paymentLinkId = item.get("paymentLinkId")
            if not paymentLinkId:
                continue
            try:
                link = self.razorpayClient.payment_link.fetch(paymentLinkId)
            except Exception as fetchErr:
                logger.error(f"Failed to fetch payment link {paymentLinkId}: {fetchErr}")
                continue
            if link["status"] == "paid":
                notes = link.get("notes", {})
                self._activatePaidDomains(
                    userId=user["userId"],
                    domains=[d.strip() for d in notes.get("domains", "").split(",") if d.strip()],
                    targetQuantity=int(notes.get("targetQuantity", 0)),
                    paymentLinkId=paymentLinkId,
                )
                changed = True
            elif link["status"] == "expired":
                item["state"] = "expired"
                changed = True
        if changed:
            cleanedPending = [item for item in pendingAdditions if item.get("state") != "expired"]
            self.client.table("Users").update({
                "pendingAdditions": cleanedPending
            }).eq("userId", user["userId"]).execute()
            user = self.client.table("Users").select("*") \
                .eq("userId", user["userId"]).execute().data[0]
        return user

    def _activatePaidDomains(self, userId: str, domains: list[str], targetQuantity: int, paymentLinkId: str) -> None:
        """
        Activate domains after confirmed payment. Idempotent -- safe to call
        multiple times for the same paymentLinkId.

        DB is updated BEFORE Razorpay subscription.edit to prevent race conditions
        with the subscription.updated webhook.

        Args:
            userId (str): The user ID.
            domains (list[str]): Domain names to activate.
            targetQuantity (int): The total subscription quantity after activation.
            paymentLinkId (str): The Razorpay Payment Link ID for idempotency.
        """
        user = self.client.table("Users") \
            .select("userId, subscribedExperts, domainCount, razorpaySubscriptionId, pendingAdditions, pendingRemovals") \
            .eq("userId", userId).execute().data[0]
        pendingAdditions = user.get("pendingAdditions") or []
        linkEntries = [item for item in pendingAdditions if item.get("paymentLinkId") == paymentLinkId]
        if linkEntries and all(item.get("state") == "activated" for item in linkEntries):
            return
        currentExperts = [e.strip() for e in (user.get("subscribedExperts") or "").split(",") if e.strip()]
        activatedDomains = []
        for domain in domains:
            if domain not in currentExperts:
                currentExperts.append(domain)
                activatedDomains.append(domain)
        for item in pendingAdditions:
            if item.get("paymentLinkId") == paymentLinkId:
                item["state"] = "activated"
        pendingRemovals = user.get("pendingRemovals") or []
        reconciledQuantity = targetQuantity - len(pendingRemovals)
        updatedDomainCount = len(currentExperts)
        self.client.table("Users").update({
            "subscribedExperts": ", ".join(currentExperts),
            "domainCount": updatedDomainCount,
            "pendingAdditions": pendingAdditions,
        }).eq("userId", userId).execute()
        subscriptionId = user["razorpaySubscriptionId"]
        if subscriptionId and reconciledQuantity >= 1:
            try:
                self.razorpayClient.subscription.edit(subscriptionId, {
                    "quantity": reconciledQuantity,
                    "schedule_change_at": "cycle_end"
                })
            except Exception as rzpErr:
                logger.error(f"Failed to update Razorpay subscription quantity for user {userId}: {rzpErr}")
        for domain in activatedDomains:
            self._auditLog(
                userId, "domain.add_activated",
                subscriptionId=subscriptionId,
                status="ACTIVATED",
                metadata={
                    "domain": domain,
                    "paymentLinkId": paymentLinkId,
                    "reconciledQuantity": reconciledQuantity,
                }
            )
        logger.info(f"Activated domains {activatedDomains} for user {userId} via payment link {paymentLinkId}")

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
                "subscriptionStart": currentTime.isoformat(),
                "subscriptionExpiry": (
                    currentTime + datetime.timedelta(days=trialDurationDays)
                ).isoformat(),
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
                status=subscription["status"].upper(),
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
                "subscriptionStart": currentTime.isoformat(),
                "subscriptionExpiry": expiry.isoformat(),
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

    def addDomains(self, domains: list[str], token: str) -> dict:
        """
        Initiate adding one or more domains to an active subscription via
        a Razorpay Payment Link for prorated charges.

        No Razorpay subscription quantity change occurs at this stage. The
        quantity is updated only after payment confirmation via the shared
        _activatePaidDomains() function.

        Args:
            domains (list[str]): The domain expert names to add.
            token (str): Authorization token.

        Returns:
            dict: Payment Link details including shortUrl for checkout.
        """
        try:
            normalizedDomains = self._normalizeAndValidateDomains(domains)
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            userId = decodedToken.get("userId")
            userEmail = decodedToken.get("email")
            user = self.client.table("Users") \
                .select("userId, subscribedExperts, domainCount, razorpaySubscriptionId, "
                        "subscriptionStatus, subscriptionStart, subscriptionExpiry, pendingAdditions, "
                        "pendingRemovals, fullName, razorpayCustomerId") \
                .eq("userId", userId) \
                .execute().data
            if not user:
                raise Exception("User not found")
            user = user[0]
            user = self._reconcilePendingAdditions(user)
            if user["subscriptionStatus"] != "ACTIVE":
                raise Exception("Subscription must be active to add domains")
            currentExperts = [e.strip() for e in (user.get("subscribedExperts") or "").split(",") if e.strip()]
            pendingAdditions = user.get("pendingAdditions") or []
            activePending = [item["domain"] for item in pendingAdditions
                             if item.get("state") not in ("failed", "activated")]
            for d in normalizedDomains:
                if d in currentExperts:
                    raise Exception(f"Domain '{d}' is already in your subscription")
                if d in activePending:
                    raise Exception(f"Domain '{d}' already has a pending addition request")
            domainCount = user["domainCount"] or len(currentExperts)
            totalAfterAdd = domainCount + len(activePending) + len(normalizedDomains)
            if totalAfterAdd > 4:
                raise Exception(f"Maximum of 4 domains. Current: {domainCount}, "
                                f"pending: {len(activePending)}, requested: {len(normalizedDomains)}")
            subscriptionId = user["razorpaySubscriptionId"]
            if not subscriptionId:
                raise Exception("No active subscription found for this user")
            plan = self.razorpayClient.plan.fetch(self.BASE_PLAN_ID)
            perDomainAmount = plan["item"]["amount"]
            cycleStart = datetime.datetime.fromisoformat(user["subscriptionStart"])
            subscriptionExpiry = datetime.datetime.fromisoformat(user["subscriptionExpiry"])
            now = datetime.datetime.utcnow()
            daysInCycle = max((subscriptionExpiry - cycleStart).days, 1)
            daysRemaining = max((subscriptionExpiry - now).days, 1)
            perDomainProrated = int((perDomainAmount / daysInCycle) * daysRemaining)
            totalProrated = perDomainProrated * len(normalizedDomains)
            domainLabel = ", ".join(normalizedDomains)
            paymentLink = self.razorpayClient.payment_link.create({
                "amount": totalProrated,
                "currency": "INR",
                "description": f"Domain upgrade: {domainLabel} (prorated {daysRemaining} days)",
                "customer": {
                    "name": user.get("fullName") or userEmail,
                    "email": userEmail,
                },
                "callback_url": f"{os.environ['API_BASE_URL']}/subscriptions/payment-callback",
                "callback_method": "get",
                "expire_by": int((now + datetime.timedelta(hours=24)).timestamp()),
                "notes": {
                    "userId": userId,
                    "domains": domainLabel,
                    "type": "domain_upgrade_proration",
                    "currentQuantity": str(domainCount),
                    "targetQuantity": str(totalAfterAdd),
                }
            })
            for d in normalizedDomains:
                pendingAdditions.append({
                    "domain": d,
                    "state": "awaiting_payment",
                    "paymentLinkId": paymentLink["id"],
                    "proratedAmount": perDomainProrated,
                    "requestedAt": str(now),
                })
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", userId).execute()
            self._auditLog(
                userId, "domain.add_requested",
                subscriptionId=subscriptionId,
                amount=totalProrated,
                status="AWAITING_PAYMENT",
                metadata={
                    "domains": normalizedDomains,
                    "perDomainProrated": perDomainProrated,
                    "totalProrated": totalProrated,
                    "daysRemaining": daysRemaining,
                    "paymentLinkId": paymentLink["id"],
                }
            )
            logger.info(f"Domain upgrade payment link created for user {userId}: {normalizedDomains}")
            return {
                "paymentLinkId": paymentLink["id"],
                "shortUrl": paymentLink["short_url"],
                "upgradeState": "awaiting_payment",
                "domains": normalizedDomains,
                "totalProratedAmount": totalProrated,
                "perDomainProratedAmount": perDomainProrated,
                "currentQuantity": domainCount,
                "targetQuantity": totalAfterAdd,
                "daysRemaining": daysRemaining,
            }
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def handlePaymentCallback(self, razorpayPaymentLinkId: str, razorpayPaymentId: str,
                               razorpayPaymentLinkStatus: str) -> str:
        """
        Handle Razorpay callback_url redirect after Payment Link payment.

        Verifies payment status with Razorpay, activates domains if paid,
        and returns a redirect URL for the frontend dashboard.

        Args:
            razorpayPaymentLinkId (str): The Payment Link ID from query params.
            razorpayPaymentId (str): The Payment ID from query params.
            razorpayPaymentLinkStatus (str): The Payment Link status from query params.

        Returns:
            str: The redirect URL for the frontend.
        """
        frontendUrl = os.environ.get("FRONTEND_URL", "")
        try:
            if razorpayPaymentLinkStatus != "paid":
                return f"{frontendUrl}/dashboard?upgrade=failed"
            link = self.razorpayClient.payment_link.fetch(razorpayPaymentLinkId)
            if link.get("status") != "paid":
                return f"{frontendUrl}/dashboard?upgrade=failed"
            notes = link.get("notes", {})
            if notes.get("type") != "domain_upgrade_proration":
                return f"{frontendUrl}/dashboard"
            self._activatePaidDomains(
                userId=notes["userId"],
                domains=[d.strip() for d in notes.get("domains", "").split(",") if d.strip()],
                targetQuantity=int(notes.get("targetQuantity", 0)),
                paymentLinkId=razorpayPaymentLinkId,
            )
            return f"{frontendUrl}/dashboard?upgrade=success"
        except Exception as e:
            logger.error(f"Payment callback handling failed for link {razorpayPaymentLinkId}: {e}")
            return f"{frontendUrl}/dashboard?upgrade=failed"

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
                .select("userId, subscribedExperts, domainCount, razorpaySubscriptionId, subscriptionStatus, pendingRemovals, pendingAdditions") \
                .eq("userId", userId) \
                .execute()
            if not userRecord.data:
                raise Exception("User not found")
            user = self._reconcilePendingAdditions(userRecord.data[0])
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
                self._auditLog(
                    userId, "domain.remove_scheduled",
                    subscriptionId=subscriptionId,
                    status="ACTIVE",
                    metadata={
                        "domain": domain,
                        "previousQuantity": domainCount,
                        "newQuantity": newQuantity,
                        "proratedAmount": 0,
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

        For Payment Link items: cancels the Razorpay Payment Link (no quantity
        revert needed since quantity was never changed at request time).
        For legacy items: reverts the Razorpay subscription quantity.

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
                algorithms=["HS256"]
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
            isPaymentLinkItem = targetItem.get("paymentLinkId") is not None
            shouldRevertQuantity = targetItem.get("state") in ("processing", "awaiting_payment", "scheduled_cycle_end")
            pendingAdditions.pop(targetIndex)
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", userId).execute()
            if isPaymentLinkItem:
                try:
                    self.razorpayClient.payment_link.cancel(targetItem["paymentLinkId"])
                except Exception as cancelErr:
                    logger.error(f"Failed to cancel payment link {targetItem['paymentLinkId']}: {cancelErr}")
            elif shouldRevertQuantity:
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
            self._auditLog(
                userId, "domain.add_cancelled",
                subscriptionId=user.get("razorpaySubscriptionId"),
                status="CANCELLED",
                metadata={
                    "domain": normalizedDomain,
                    "previousQuantity": targetItem.get("targetQuantity", 0),
                    "newQuantity": (user["domainCount"] or 0),
                    "proratedAmount": 0,
                    "effectiveAt": "immediate"
                }
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
                status="INITIATED"
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
                "status": invoiceData.get("status", "paid").upper(),
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
