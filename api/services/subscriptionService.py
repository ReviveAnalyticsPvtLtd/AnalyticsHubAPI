"""
subscriptionService.py

This module provides the SubscriptionService class, which encapsulates
all business logic related to user subscriptions, including free trials,
Razorpay token-based recurring payment management, customer management,
and payment audit logging.
"""

__version__ = "1.0.0"
__author__ = "Rauhan Ahmed Siddiqui"
__all__ = ["subscriptionService"]


from dateutil.relativedelta import relativedelta
from utils.exceptionHandler import CustomException
from utils.logger import logger
from api.commons import client
from jose import jwt
import requests
import razorpay
import datetime
from dateutil import parser
import hashlib
import hmac
import json
import os


class SubscriptionService:
    """
    Service class for user subscription management.

    Handles free trials, Razorpay token-based recurring payments,
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

    def _getOrCreateRazorpayCustomer(self, userId: str, email: str, name: str, contact: str = "") -> str:
        """
        Retrieve existing or create a new Razorpay Customer for the user.

        Args:
            userId (str): The internal user ID.
            email (str): The user's email address.
            name (str): The user's full name.
            contact (str): The user's phone number.

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
            "contact": contact,
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

    _STALE_ORDER_THRESHOLD_MINUTES = 30

    def _reconcilePendingAdditions(self, user: dict) -> dict:
        """
        Reconcile awaiting_payment entries against Razorpay Order status.

        Activates paid domains, removes expired entries, and cleans up
        stale orders older than _STALE_ORDER_THRESHOLD_MINUTES that the
        user abandoned (closed the checkout without paying). Called at
        the start of addDomains() and removeDomain() to resolve any
        missed activations.

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
            orderId = item.get("orderId")
            if not orderId:
                continue
            try:
                order = self.razorpayClient.order.fetch(orderId)
            except Exception as fetchErr:
                logger.error(f"Failed to fetch order {orderId}: {fetchErr}")
                continue
            if order["status"] == "paid":
                notes = order.get("notes", {})
                self._activatePaidDomains(
                    userId=user["userId"],
                    domains=[d.strip() for d in notes.get("domains", "").split(",") if d.strip()],
                    targetQuantity=int(notes.get("targetQuantity", 0)),
                    referenceId=orderId,
                )
                changed = True
            elif order["status"] in ("expired", "cancelled"):
                item["state"] = "expired"
                changed = True
            elif order["status"] == "created":
                # Order still unpaid — expire if older than threshold
                requestedAt = item.get("requestedAt")
                if requestedAt:
                    try:
                        age = datetime.datetime.utcnow() - parser.isoparse(requestedAt)
                        if age > datetime.timedelta(minutes=self._STALE_ORDER_THRESHOLD_MINUTES):
                            item["state"] = "expired"
                            changed = True
                            logger.info(
                                f"Expired stale awaiting_payment order {orderId} "
                                f"for user {user['userId']} (age: {age})"
                            )
                    except (ValueError, TypeError):
                        pass
        if changed:
            cleanedPending = [item for item in pendingAdditions if item.get("state") not in ("expired", "activated")]
            self.client.table("Users").update({
                "pendingAdditions": cleanedPending
            }).eq("userId", user["userId"]).execute()
            user = self.client.table("Users").select("*") \
                .eq("userId", user["userId"]).execute().data[0]
        return user

    def _activatePaidDomains(self, userId: str, domains: list[str], targetQuantity: int, referenceId: str) -> None:
        """
        Activate domains after confirmed payment. Idempotent -- safe to call
        multiple times for the same referenceId (order ID or legacy payment link ID).

        The billing engine picks up the updated domainCount at renewal, so no
        Razorpay API call is needed here.

        Args:
            userId (str): The user ID.
            domains (list[str]): Domain names to activate.
            targetQuantity (int): The expected total domain count after activation.
            referenceId (str): The Razorpay Order ID (or legacy Payment Link ID) for idempotency.
        """
        user = self.client.table("Users") \
            .select("userId, subscribedExperts, domainCount, pendingAdditions") \
            .eq("userId", userId).execute().data[0]
        pendingAdditions = user.get("pendingAdditions") or []
        matchKey = "orderId" if any(item.get("orderId") == referenceId for item in pendingAdditions) else "paymentLinkId"
        matchEntries = [item for item in pendingAdditions if item.get(matchKey) == referenceId]
        if matchEntries and all(item.get("state") == "activated" for item in matchEntries):
            return
        currentExperts = [e.strip() for e in (user.get("subscribedExperts") or "").split(",") if e.strip()]
        activatedDomains = []
        for domain in domains:
            if domain not in currentExperts:
                currentExperts.append(domain)
                activatedDomains.append(domain)
        for item in pendingAdditions:
            if item.get(matchKey) == referenceId:
                item["state"] = "activated"
        updatedDomainCount = len(currentExperts)
        self.client.table("Users").update({
            "subscribedExperts": ", ".join(currentExperts),
            "domainCount": updatedDomainCount,
            "pendingAdditions": pendingAdditions,
        }).eq("userId", userId).execute()
        for domain in activatedDomains:
            self._auditLog(
                userId, "domain.add_activated",
                status="ACTIVATED",
                metadata={
                    "domain": domain,
                    "referenceId": referenceId,
                    "newDomainCount": updatedDomainCount,
                }
            )
        logger.info(f"Activated domains {activatedDomains} for user {userId} via {referenceId}")

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
                "subscriptionDaysLeft": trialDurationDays,
                "subscribedExperts": "banking, manufacturing, supplychain, telecom",
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

    def createSubscription(self, domains: list[str], contact: str, token: str) -> dict:
        """
        Create a Razorpay Order for tokenization-based subscription checkout.

        The Order captures the user's card details and creates a saved mandate
        (token) for future recurring charges by the billing engine.

        Args:
            domains (list[str]): Domain expert names the user is subscribing to.
            contact (str): User's phone number for Razorpay checkout and RBI compliance.
            token (str): Authorization token.

        Returns:
            dict: Data required to open Razorpay checkout.
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
            userRecord = self.client.table("Users").select("fullName").eq("userId", userId).execute()
            fullName = userRecord.data[0]["fullName"] if userRecord.data else userEmail
            customerId = self._getOrCreateRazorpayCustomer(userId, userEmail, fullName, contact)
            self.client.table("Users").update({"phoneNumber": contact}).eq("userId", userId).execute()
            quantity = len(normalizedDomains)
            plan = self.razorpayClient.plan.fetch(self.BASE_PLAN_ID)
            perDomainAmount = plan["item"]["amount"]
            totalAmount = perDomainAmount * quantity
            farFutureEpoch = int((datetime.datetime.utcnow() + datetime.timedelta(days=365 * 10)).timestamp())
            order = self.razorpayClient.order.create({
                "amount": totalAmount,
                "currency": "INR",
                "customer_id": customerId,
                "method": "card",
                "token": {
                    "max_amount": 1500000,
                    "expire_at": farFutureEpoch,
                    "frequency": "monthly",
                },
                "notes": {
                    "userId": userId,
                    "type": "initial_subscription",
                    "domains": ", ".join(normalizedDomains),
                },
            })
            self._auditLog(
                userId, "subscription.created",
                status="CREATED",
                metadata={
                    "orderId": order["id"],
                    "domains": normalizedDomains,
                    "quantity": quantity,
                    "amount": totalAmount,
                }
            )
            return {
                "userId": userId,
                "userEmail": userEmail,
                "userContact": contact,
                "userName": fullName,
                "razorpayKey": os.environ["RAZORPAY_KEY_ID"],
                "orderId": order["id"],
                "customerId": customerId,
                "status": order["status"],
                "quantity": quantity,
                "domains": normalizedDomains,
            }
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception
        
    def verifySubscription(self, payload: dict, token: str) -> None:
        """
        Verify Razorpay Order checkout signature and activate the subscription.

        Performs HMAC SHA256 verification using Razorpay API secret. On success,
        extracts the saved token (mandate) from the payment entity, sets cycle
        dates using the Anchor Date strategy, and activates the subscription.

        Args:
            payload (dict): Razorpay checkout response payload.
            token (str): Authorization token.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            userId = decodedToken.get("userId")
            paymentId = payload.get("razorpayPaymentId")
            orderId = payload.get("razorpayOrderId")
            signature = payload.get("razorpaySignature")
            domains = payload.get("domains")
            if not all([paymentId, orderId, signature, userId, domains]):
                raise Exception("Missing Razorpay verification fields")
            normalizedDomains = self._normalizeAndValidateDomains(domains)
            message = f"{orderId}|{paymentId}"
            expectedSignature = hmac.new(
                os.environ["RAZORPAY_KEY_SECRET"].encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expectedSignature, signature):
                raise Exception("Invalid Razorpay signature")
            payment = self.razorpayClient.payment.fetch(paymentId)
            tokenId = payment.get("token_id")
            if not tokenId:
                raise Exception("Payment did not produce a recurring token (token_id is missing)")
            userRecord = self.client.table("Users").select("razorpayCustomerId").eq("userId", userId).execute()
            customerId = userRecord.data[0].get("razorpayCustomerId") if userRecord.data else None
            if not customerId:
                raise Exception("No Razorpay customer found — cannot validate token")
            tokenEntity = self.razorpayClient.token.fetch(customerId, tokenId)
            recurringDetails = tokenEntity.get("recurring_details") or {}
            recurringStatus = recurringDetails.get("status", "")
            isRecurring = tokenEntity.get("recurring", False)
            UNUSABLE_STATES = {"cancelled", "expired", "rejected"}
            if recurringStatus in UNUSABLE_STATES:
                raise Exception(
                    f"Token {tokenId} is not usable for recurring payments "
                    f"(recurring_details.status={recurringStatus})"
                )
            if not isRecurring:
                raise Exception(
                    f"Token {tokenId} is not recurring-enabled (recurring={isRecurring})"
                )
            logger.info(
                f"Token {tokenId} validated for user {userId}: "
                f"recurring={isRecurring}, recurring_details.status={recurringStatus}"
            )
            currentTime = datetime.datetime.utcnow()
            anchorDay = currentTime.day
            expiry = currentTime + relativedelta(months=1)
            daysLeft = (expiry.date() - currentTime.date()).days
            self.client.table("Users").update({
                "razorpayTokenId": tokenId,
                "subscriptionAnchorDay": anchorDay,
                "subscriptionPlan": "pro",
                "subscriptionStatus": "ACTIVE",
                "subscriptionStart": currentTime.isoformat(),
                "subscriptionExpiry": expiry.isoformat(),
                "subscriptionDaysLeft": daysLeft,
                "subscribedExperts": ", ".join(normalizedDomains),
                "domainCount": len(normalizedDomains),
                "recurringFailures": 0,
                "pendingRemovals": [],
                "pendingAdditions": [],
            }).eq("userId", userId).execute()
            self._auditLog(
                userId, "subscription.verified",
                paymentId=paymentId,
                status="ACTIVE",
                metadata={
                    "orderId": orderId,
                    "tokenId": tokenId,
                    "tokenRecurringStatus": recurringStatus,
                    "domains": normalizedDomains,
                    "quantity": len(normalizedDomains),
                    "anchorDay": anchorDay,
                }
            )
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def addDomains(self, domains: list[str], token: str) -> dict:
        """
        Initiate adding one or more domains to an active subscription via
        a Razorpay Order for prorated charges.

        Domains are activated only after payment confirmation via the
        verifyDomainUpgrade() method.

        Args:
            domains (list[str]): The domain expert names to add.
            token (str): Authorization token.

        Returns:
            dict: Order details required for Razorpay embedded checkout.
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
                .select("userId, subscribedExperts, domainCount, razorpayTokenId, "
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
                    # Self-healing: resolve the stale entry instead of blocking
                    staleItem = next(
                        (item for item in pendingAdditions
                         if item["domain"] == d and item.get("state") == "awaiting_payment"),
                        None
                    )
                    if staleItem:
                        staleOrderId = staleItem.get("orderId")
                        staleOrderStatus = None
                        if staleOrderId:
                            try:
                                staleOrder = self.razorpayClient.order.fetch(staleOrderId)
                                staleOrderStatus = staleOrder["status"]
                            except Exception:
                                staleOrderStatus = "fetch_failed"
                        if staleOrderStatus == "paid":
                            # Payment went through but activation was missed — activate now
                            notes = staleOrder.get("notes", {})
                            self._activatePaidDomains(
                                userId=userId,
                                domains=[x.strip() for x in notes.get("domains", "").split(",") if x.strip()],
                                targetQuantity=int(notes.get("targetQuantity", 0)),
                                referenceId=staleOrderId,
                            )
                            # Refresh state after activation
                            user = self.client.table("Users") \
                                .select("userId, subscribedExperts, domainCount, razorpayTokenId, "
                                        "subscriptionStatus, subscriptionStart, subscriptionExpiry, pendingAdditions, "
                                        "pendingRemovals, fullName, razorpayCustomerId") \
                                .eq("userId", userId).execute().data[0]
                            currentExperts = [e.strip() for e in (user.get("subscribedExperts") or "").split(",") if e.strip()]
                            pendingAdditions = user.get("pendingAdditions") or []
                            if d in currentExperts:
                                # Already activated — skip this domain
                                continue
                        else:
                            # Order is unpaid / expired / fetch failed — remove stale entry
                            pendingAdditions.remove(staleItem)
                            activePending = [item["domain"] for item in pendingAdditions
                                             if item.get("state") not in ("failed", "activated")]
                            self._auditLog(
                                userId, "domain.stale_pending_cleared",
                                status="CLEARED",
                                metadata={
                                    "domain": d,
                                    "staleOrderId": staleOrderId,
                                    "staleOrderStatus": staleOrderStatus,
                                }
                            )
                            logger.info(
                                f"Cleared stale pending addition for domain '{d}', "
                                f"order {staleOrderId} (status={staleOrderStatus})"
                            )
                    else:
                        raise Exception(f"Domain '{d}' already has a pending addition request")
            domainCount = user["domainCount"] or len(currentExperts)
            totalAfterAdd = domainCount + len(activePending) + len(normalizedDomains)
            if totalAfterAdd > 4:
                raise Exception(f"Maximum of 4 domains. Current: {domainCount}, "
                                f"pending: {len(activePending)}, requested: {len(normalizedDomains)}")
            if not user.get("razorpayTokenId"):
                raise Exception("No active subscription found for this user")
            plan = self.razorpayClient.plan.fetch(self.BASE_PLAN_ID)
            perDomainAmount = plan["item"]["amount"]
            cycleStart = parser.isoparse(user["subscriptionStart"])
            subscriptionExpiry = parser.isoparse(user["subscriptionExpiry"])
            now = datetime.datetime.utcnow()
            daysInCycle = max((subscriptionExpiry - cycleStart).days, 1)
            daysRemaining = max((subscriptionExpiry - now).days, 1)
            perDomainProrated = int((perDomainAmount / daysInCycle) * daysRemaining)
            totalProrated = perDomainProrated * len(normalizedDomains)
            domainLabel = ", ".join(normalizedDomains)
            order = self.razorpayClient.order.create({
                "amount": totalProrated,
                "currency": "INR",
                "customer_id": user.get("razorpayCustomerId"),
                "notes": {
                    "userId": userId,
                    "domains": domainLabel,
                    "type": "domain_upgrade_proration",
                    "currentQuantity": str(domainCount),
                    "targetQuantity": str(totalAfterAdd),
                },
            })
            for d in normalizedDomains:
                pendingAdditions.append({
                    "domain": d,
                    "state": "awaiting_payment",
                    "orderId": order["id"],
                    "proratedAmount": perDomainProrated,
                    "requestedAt": str(now),
                })
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", userId).execute()
            self._auditLog(
                userId, "domain.add_requested",
                amount=totalProrated,
                status="AWAITING_PAYMENT",
                metadata={
                    "domains": normalizedDomains,
                    "perDomainProrated": perDomainProrated,
                    "totalProrated": totalProrated,
                    "daysRemaining": daysRemaining,
                    "orderId": order["id"],
                }
            )
            logger.info(f"Domain upgrade order created for user {userId}: {normalizedDomains}")
            return {
                "razorpayKey": os.environ["RAZORPAY_KEY_ID"],
                "orderId": order["id"],
                "currency": order["currency"],
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

    def verifyDomainUpgrade(self, payload: dict, token: str) -> None:
        """
        Verify Razorpay Order checkout signature and activate the added domains.

        Performs HMAC SHA256 verification using Razorpay API secret. On success,
        activates the paid domains via the idempotent _activatePaidDomains().

        Args:
            payload (dict): Razorpay checkout response payload.
            token (str): Authorization token.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            userId = decodedToken.get("userId")
            paymentId = payload.get("razorpayPaymentId")
            orderId = payload.get("razorpayOrderId")
            signature = payload.get("razorpaySignature")
            domains = payload.get("domains")
            if not all([paymentId, orderId, signature, userId, domains]):
                raise Exception("Missing Razorpay verification fields")
            normalizedDomains = self._normalizeAndValidateDomains(domains)
            message = f"{orderId}|{paymentId}"
            expectedSignature = hmac.new(
                os.environ["RAZORPAY_KEY_SECRET"].encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expectedSignature, signature):
                raise Exception("Invalid Razorpay signature")
            user = self.client.table("Users") \
                .select("userId, domainCount, pendingAdditions") \
                .eq("userId", userId).execute().data
            if not user:
                raise Exception("User not found")
            user = user[0]
            pendingAdditions = user.get("pendingAdditions") or []
            targetQuantity = 0
            for item in pendingAdditions:
                if item.get("orderId") == orderId:
                    targetQuantity += 1
            currentCount = user.get("domainCount") or 0
            self._activatePaidDomains(
                userId=userId,
                domains=normalizedDomains,
                targetQuantity=currentCount + targetQuantity,
                referenceId=orderId,
            )
            self._auditLog(
                userId, "domain.upgrade_verified",
                paymentId=paymentId,
                status="VERIFIED",
                metadata={
                    "orderId": orderId,
                    "domains": normalizedDomains,
                }
            )
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception

    def removeDomain(self, domains: list[str], token: str) -> dict:
        """
        Schedule one or more domain removals at the end of the current billing cycle.

        Purely a DB operation -- the billing engine reads the reduced domainCount
        at renewal. The user retains access until cycle end. At least one domain
        must remain active.

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
                algorithms=["HS256"]
            )
            userId = decodedToken.get("userId")
            userRecord = self.client.table("Users") \
                .select("userId, subscribedExperts, domainCount, subscriptionStatus, pendingRemovals, pendingAdditions") \
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
                    raise CustomException(
                        ValueError(f"Domain '{domain}' is already scheduled for removal"),
                        statusCode=409,
                        uiMessage=f"Domain '{domain}' is already scheduled for removal."
                    )
            activeDomains = [d for d in currentExperts if d not in pendingRemovals]
            if len(activeDomains) - len(normalizedDomains) < 1:
                raise Exception("Cannot remove all domains. At least one must remain active. Use cancel subscription instead.")
            pendingRemovals.extend(normalizedDomains)
            self.client.table("Users").update({
                "pendingRemovals": pendingRemovals
            }).eq("userId", userId).execute()
            domainCount = user["domainCount"] or len(currentExperts)
            for domain in normalizedDomains:
                self._auditLog(
                    userId, "domain.remove_scheduled",
                    status="ACTIVE",
                    metadata={
                        "domain": domain,
                        "currentDomainCount": domainCount,
                        "effectiveAt": "cycle_end",
                    }
                )
            logger.info(f"Domains {normalizedDomains} scheduled for removal at cycle_end for user {userId}")
            return {
                "currentDomains": currentExperts,
                "pendingRemovals": pendingRemovals,
                "effectiveAt": "cycle_end",
            }
        except CustomException:
            raise
        except Exception as e:
            exception = CustomException(e)
            logger.error(exception)
            raise exception




    def cancelPendingAddition(self, domain: str, token: str) -> dict:
        """
        Cancel a pending domain addition request.

        Removes the pending entry from the DB. Razorpay Orders cannot be
        programmatically cancelled; they expire naturally.

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
                .select("pendingAdditions, domainCount") \
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
            pendingAdditions.pop(targetIndex)
            self.client.table("Users").update({
                "pendingAdditions": pendingAdditions
            }).eq("userId", userId).execute()
            self._auditLog(
                userId, "domain.add_cancelled",
                status="CANCELLED",
                metadata={
                    "domain": normalizedDomain,
                    "currentDomainCount": user["domainCount"] or 0,
                    "effectiveAt": "immediate",
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
        Schedule cancellation at the end of the current billing cycle.

        Sets subscriptionStatus to PENDING_CANCELLATION. The billing engine
        skips users with this status at renewal, and resolve_subscription_status()
        transitions them to EXPIRED once subscriptionExpiry passes.

        Args:
            token (str): Authorization token.

        Returns:
            dict: Cancellation confirmation with effective timing.
        """
        try:
            decodedToken = jwt.decode(
                token,
                os.environ["SECRET_KEY"],
                algorithms=["HS256"]
            )
            userId = decodedToken.get("userId")
            userRecord = self.client.table("Users") \
                .select("subscriptionStatus") \
                .eq("userId", userId) \
                .execute()
            if not userRecord.data:
                raise Exception("User not found")
            currentStatus = userRecord.data[0].get("subscriptionStatus")
            if currentStatus not in ("ACTIVE", "TRIAL"):
                raise Exception("No active subscription found for this user")
            self.client.table("Users").update({
                "subscriptionStatus": "PENDING_CANCELLATION",
            }).eq("userId", userId).execute()
            self._auditLog(
                userId, "subscription.cancellation_scheduled",
                status="PENDING_CANCELLATION",
                metadata={"cancel_at_cycle_end": True}
            )
            logger.info(f"Subscription scheduled for cancellation at cycle end for user {userId}")
            return {"cancelled": True, "effectiveAt": "cycle_end"}
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
                .select("razorpayCustomerId") \
                .eq("userId", userId) \
                .execute()
            if not userRecord.data:
                raise Exception("User not found")
            customerId = userRecord.data[0].get("razorpayCustomerId")
            if not customerId:
                raise Exception("No Razorpay customer found for this user")
            payment = self.razorpayClient.payment.fetch(paymentId)
            paymentCustomerId = payment.get("customer_id")
            if not paymentCustomerId or paymentCustomerId != customerId:
                logger.warning(f"Unauthorized refund attempt blocked for user {userId}, payment {paymentId}")
                raise Exception("Payment does not belong to this user")
            refundParams = {}
            if amount is not None:
                refundParams["amount"] = amount
            result = self.razorpayClient.payment.refund(paymentId, refundParams)
            self._auditLog(
                userId, "refund.initiated",
                paymentId=paymentId,
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
