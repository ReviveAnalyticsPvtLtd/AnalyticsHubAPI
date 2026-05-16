"""
billingTask.py

Daily billing engine for the token-based recurring payment system.
Runs as a Celery Beat task at midnight UTC.

Queries subscriptions whose current_period_end falls within 48 hours,
validates token health, enforces RBI silent-charge thresholds, persists
payment_attempts before any provider call, and creates a Razorpay Order
followed by create_recurring_payment against the user's saved token.

All post-payment state changes (expiry bump, invoice creation, dunning)
are handled by the payment.captured and payment.failed webhook handlers
in webhookService.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = ["DailyBillingTask"]

from api.services.billingConfig import MAX_SILENT_RECURRING_AMOUNT_PAISE
from supabase import create_client
from utils.logger import logger
import razorpay
import datetime
import requests
import redis
import time
import json
import os


def _getSupabaseClient():
    """
    Create and return a Supabase client using environment variables.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


class DailyBillingTask:
    """
    Daily billing engine that queues recurring charges for token-based
    subscriptions approaching renewal.

    Phase 2 hardening includes:
        - RBI silent-charge threshold guard.
        - Token health validation before every charge attempt.
        - Period-based idempotency (DB uniqueness + Redis lock).
        - payment_attempts ledger for reconciliation readiness.
    """

    _CHARGE_LOCK_TTL_SECONDS = 72 * 60 * 60
    _PRICE_CACHE_KEY = "billing:base_price_amount"
    _PRICE_CACHE_TTL = 24 * 60 * 60
    _PRICE_FETCH_RETRIES = 3
    _TOKEN_USABLE_STATUSES = {"confirmed", "activated", "active"}

    def __init__(self):
        self.client = _getSupabaseClient()
        self.razorpayClient = razorpay.Client(
            auth=(
                os.environ.get("RAZORPAY_KEY_ID", ""),
                os.environ.get("RAZORPAY_KEY_SECRET", ""),
            )
        )
        self.BASE_PLAN_ID = os.environ.get("RAZORPAY_PRO_PLAN_ID", "")
        self.redis = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            password=os.environ.get("REDIS_PASSWORD", None),
            decode_responses=True,
        )

    def execute(self) -> dict:
        """
        Run the daily billing cycle.

        Returns:
            dict: Counts of queued, skipped, and errored charges.
        """
        logger.info("Daily billing task started")
        results = self._chargeExecution()
        logger.info(
            f"Daily billing task completed: {results['queued']} queued, "
            f"{results['skipped']} skipped, {results['errors']} errors"
        )
        return results

    def _getBasePriceAmount(self) -> int:
        """
        Fetch the per-domain price in paise from the Razorpay plan.
        Retries with backoff and falls back to a Redis-cached value
        if the API is unreachable.

        Returns:
            int: Amount in paise per domain per cycle.

        Raises:
            RuntimeError: If the API fails and no cached price exists.
        """
        lastError = None
        for attempt in range(1, self._PRICE_FETCH_RETRIES + 1):
            try:
                plan = self.razorpayClient.plan.fetch(self.BASE_PLAN_ID)
                amount = plan["item"]["amount"]
                self.redis.set(
                    self._PRICE_CACHE_KEY, str(amount), ex=self._PRICE_CACHE_TTL
                )
                return amount
            except Exception as e:
                lastError = e
                logger.warning(
                    f"Plan fetch attempt {attempt}/{self._PRICE_FETCH_RETRIES} failed: {e}"
                )
                if attempt < self._PRICE_FETCH_RETRIES:
                    time.sleep(2**attempt)

        cached = self.redis.get(self._PRICE_CACHE_KEY)
        if cached:
            logger.warning(f"Using cached base price: {cached}")
            return int(cached)

        raise RuntimeError(
            f"Cannot fetch base price and no cached value available: {lastError}"
        )

    def _auditLog(self, userId: str, eventType: str, **kwargs) -> None:
        """
        Insert a row into the SubscriptionLog table.

        Args:
            userId (str): The user ID.
            eventType (str): The event type identifier.
            **kwargs: status and metadata fields.
        """
        try:
            status = kwargs.pop("status", None)
            metadata = kwargs.pop("metadata", None) or {}
            metadata.update(kwargs)
            self.client.table("SubscriptionLog").insert(
                {
                    "userId": userId,
                    "eventType": eventType,
                    "status": status,
                    "metadata": metadata if metadata else None,
                }
            ).execute()
        except Exception as e:
            logger.error(
                f"SubscriptionLog insert failed for user {userId}, event {eventType}: {e}"
            )

    def _validateTokenHealth(self, userId: str, tokenId: str,
                             customerId: str, chargeAmount: int) -> dict:
        """
        Validate the Razorpay token is healthy before attempting a charge.

        Checks:
            - Token exists and belongs to the claimed customer.
            - Token is recurring-capable.
            - Token status is usable (confirmed/activated/active).
            - Charge amount does not exceed token max_amount.

        Args:
            userId (str): Internal user ID (for logging).
            tokenId (str): Razorpay token ID.
            customerId (str): Razorpay customer ID.
            chargeAmount (int): Intended charge amount in paise.

        Returns:
            dict: {"valid": True} or {"valid": False, "reason": str}.
        """
        try:
            token = self.razorpayClient.token.fetch(customerId, tokenId)
        except Exception as e:
            return {"valid": False, "reason": f"token_fetch_failed: {e}"}

        if token.get("customer_id") != customerId:
            return {"valid": False, "reason": "token_customer_mismatch"}

        if not token.get("recurring"):
            return {"valid": False, "reason": "token_not_recurring"}

        tokenStatus = (token.get("recurring_details", {}).get("status") or
                       token.get("status") or "").lower()
        if tokenStatus not in self._TOKEN_USABLE_STATUSES:
            return {"valid": False, "reason": f"token_status_unusable: {tokenStatus}"}

        maxAmount = token.get("max_amount")
        if maxAmount and chargeAmount > int(maxAmount):
            return {
                "valid": False,
                "reason": f"amount_exceeds_token_max: {chargeAmount} > {maxAmount}"
            }

        return {"valid": True}

    def _createPaymentAttempt(self, userId: str, subscriptionId: str,
                              periodStart: str, periodEnd: str,
                              cycleKey: str, amount: int,
                              idempotencyKey: str) -> dict | None:
        """
        Persist a payment_attempts row before the provider call.

        Uses the DB partial unique index on (user_id, period_start,
        period_end, attempt_type) WHERE status IN ('created',
        'pending_provider_ack', 'authorized') to prevent duplicate
        unresolved attempts for the same billing period.

        Args:
            userId (str): Internal user ID.
            subscriptionId (str): The subscription UUID.
            periodStart (str): ISO period start.
            periodEnd (str): ISO period end.
            cycleKey (str): Human-readable cycle key (e.g. '2026-05').
            amount (int): Charge amount in paise.
            idempotencyKey (str): Unique key for this attempt.

        Returns:
            dict | None: The created row, or None if duplicate blocked.
        """
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            result = self.client.table("payment_attempts").insert({
                "user_id": userId,
                "subscription_id": subscriptionId,
                "period_start": periodStart,
                "period_end": periodEnd,
                "cycle_key": cycleKey,
                "attempt_type": "token_debit",
                "status": "created",
                "amount": amount,
                "currency": "INR",
                "attempted_at": now,
                "idempotency_key": idempotencyKey,
            }).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            errorStr = str(e).lower()
            if "duplicate key" in errorStr or "unique constraint" in errorStr or "23505" in errorStr:
                logger.info(
                    f"Duplicate payment_attempt blocked for user {userId}, "
                    f"cycle {cycleKey} — already has unresolved attempt"
                )
                return None
            raise

    def _updatePaymentAttemptStatus(self, attemptId: str, status: str,
                                    providerPaymentId: str = None,
                                    providerOrderId: str = None,
                                    failureReason: str = None) -> None:
        """
        Transition a payment_attempts row to a new status.

        Args:
            attemptId (str): The UUID of the payment attempt.
            status (str): New status value.
            providerPaymentId (str): Razorpay payment ID, if available.
            providerOrderId (str): Razorpay order ID, if available.
            failureReason (str): Reason string for failed/precheck_failed.
        """
        updateData = {"status": status}
        if providerPaymentId:
            updateData["provider_payment_id"] = providerPaymentId
        if providerOrderId:
            updateData["provider_order_id"] = providerOrderId
        if failureReason:
            updateData["failure_reason"] = failureReason[:2000]
        if status in ("captured", "failed", "expired", "cancelled", "precheck_failed"):
            updateData["completed_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
        try:
            self.client.table("payment_attempts").update(
                updateData
            ).eq("id", attemptId).execute()
        except Exception as e:
            logger.error(f"Failed to update payment_attempt {attemptId}: {e}")

    def _notifyPaymentMethodUpdateRequired(self, user: dict, reason: str) -> None:
        """
        Trigger notification flow when saved token is unusable and user action
        is required to continue recurring billing.

        Uses PAYMENT_METHOD_UPDATE_REQUIRED_EMAIL_URL when available, otherwise
        falls back to PAYMENT_FAILED_EMAIL_URL.

        Args:
            user (dict): User row with at least email/fullName.
            reason (str): Machine-friendly precheck failure reason.
        """
        email = user.get("email")
        if not email:
            logger.info("Payment-method update notification skipped: user email missing")
            return

        emailUrl = (
            os.environ.get("PAYMENT_METHOD_UPDATE_REQUIRED_EMAIL_URL")
            or os.environ.get("PAYMENT_FAILED_EMAIL_URL")
        )
        if not emailUrl:
            logger.info(
                "Payment-method update notification skipped: "
                "no PAYMENT_METHOD_UPDATE_REQUIRED_EMAIL_URL/PAYMENT_FAILED_EMAIL_URL configured"
            )
            return

        payload = {
            "email": email,
            "name": user.get("fullName", ""),
            "reason": "payment_method_update_required",
            "detail": reason,
        }

        try:
            requests.post(
                url=emailUrl,
                data=json.dumps(payload),
                headers={"Authorization": f"Bearer {os.environ['SUPABASE_KEY']}"},
                timeout=10,
            )
        except Exception as e:
            logger.error(
                f"Failed to send payment-method update notification to {email}: {e}"
            )

    def _chargeExecution(self) -> dict:
        """
        Queue recurring charges for subscriptions whose current_period_end
        falls within 48 hours.

        Enforces threshold guard, token precheck, period-based idempotency,
        and payment_attempts ledger persistence before any provider call.

        Returns:
            dict: Counts of queued, skipped, and errored charges.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        chargeWindowEnd = now + datetime.timedelta(hours=48)

        dueSubscriptions = (
            self.client.table("subscriptions")
            .select("id, user_id, current_period_start, current_period_end, status, billing_mode")
            .eq("status", "active")
            .eq("billing_mode", "monthly_recurring")
            .gte("current_period_end", now.isoformat())
            .lte("current_period_end", chargeWindowEnd.isoformat())
            .execute()
            .data
        )

        if not dueSubscriptions:
            logger.info("No users due for charge queuing")
            return {"queued": 0, "skipped": 0, "errors": 0}

        basePriceAmount = self._getBasePriceAmount()
        queued = 0
        skipped = 0
        errors = 0

        for subscription in dueSubscriptions:
            userId = subscription["user_id"]
            subscriptionId = subscription["id"]
            periodStart = subscription.get("current_period_start", "")
            periodEnd = subscription.get("current_period_end", "")

            try:
                userRows = (
                    self.client.table("Users")
                    .select(
                        "userId, email, fullName, domainCount, razorpayTokenId, "
                        "razorpayCustomerId, subscriptionAnchorDay, recurringFailures, "
                        "pendingRemovals, subscribedExperts, phoneNumber"
                    )
                    .eq("userId", userId)
                    .not_.is_("razorpayTokenId", "null")
                    .limit(1)
                    .execute()
                    .data
                )
                if not userRows:
                    logger.warning(
                        f"Skipping subscription {userId}: user missing or token unavailable"
                    )
                    skipped += 1
                    continue

                user = userRows[0]

                if (user.get("recurringFailures") or 0) >= 3:
                    logger.info(
                        f"User {userId} has reached max recurring failures, skipping"
                    )
                    skipped += 1
                    continue

                self._processPendingRemovals(user)

                refreshed = (
                    self.client.table("Users")
                    .select("domainCount, subscribedExperts")
                    .eq("userId", userId)
                    .execute()
                    .data[0]
                )
                domainCount = refreshed.get("domainCount") or 0

                if domainCount < 1:
                    logger.warning(
                        f"User {userId} has 0 domains after removal processing, skipping"
                    )
                    skipped += 1
                    continue

                amount = domainCount * basePriceAmount
                tokenId = user["razorpayTokenId"]
                customerId = user["razorpayCustomerId"]

                cycleKey = f"{periodStart[:10]}_{periodEnd[:10]}" if periodEnd else now.strftime("%Y-%m")
                lockKey = f"billing:charge:{userId}:{cycleKey}"
                acquired = self.redis.set(
                    lockKey, "1", nx=True, ex=self._CHARGE_LOCK_TTL_SECONDS
                )
                if not acquired:
                    logger.info(
                        f"User {userId} already has a charge lock for cycle {cycleKey}, skipping"
                    )
                    skipped += 1
                    continue

                idempotencyKey = f"billing:{userId}:{cycleKey}:{int(time.time())}"
                attempt = self._createPaymentAttempt(
                    userId=userId,
                    subscriptionId=subscriptionId,
                    periodStart=periodStart,
                    periodEnd=periodEnd,
                    cycleKey=cycleKey,
                    amount=amount,
                    idempotencyKey=idempotencyKey,
                )
                if not attempt:
                    self.redis.delete(lockKey)
                    skipped += 1
                    continue

                attemptId = attempt["id"]

                if amount > MAX_SILENT_RECURRING_AMOUNT_PAISE:
                    self._updatePaymentAttemptStatus(
                        attemptId=attemptId,
                        status="precheck_failed",
                        failureReason=(
                            "threshold_exceeded_requires_authenticated_flow:"
                            f"{amount}>{MAX_SILENT_RECURRING_AMOUNT_PAISE}"
                        ),
                    )
                    self._auditLog(
                        userId, "billing.threshold_redirected",
                        status="REDIRECTED",
                        metadata={
                            "amount": amount,
                            "threshold": MAX_SILENT_RECURRING_AMOUNT_PAISE,
                            "domainCount": domainCount,
                            "attemptId": attemptId,
                        },
                    )
                    logger.info(
                        f"User {userId} amount {amount} exceeds silent threshold "
                        f"{MAX_SILENT_RECURRING_AMOUNT_PAISE}, requires authenticated collection"
                    )
                    self.redis.delete(lockKey)
                    skipped += 1
                    continue

                tokenCheck = self._validateTokenHealth(
                    userId, tokenId, customerId, amount
                )
                if not tokenCheck["valid"]:
                    self._updatePaymentAttemptStatus(
                        attemptId=attemptId,
                        status="precheck_failed",
                        failureReason=tokenCheck["reason"],
                    )
                    self._auditLog(
                        userId, "billing.token_precheck_failed",
                        status="PRECHECK_FAILED",
                        metadata={
                            "reason": tokenCheck["reason"],
                            "tokenId": tokenId,
                            "attemptId": attemptId,
                        },
                    )
                    self._notifyPaymentMethodUpdateRequired(
                        user=user,
                        reason=tokenCheck["reason"],
                    )
                    logger.warning(
                        f"Token precheck failed for user {userId}: {tokenCheck['reason']}"
                    )
                    self.redis.delete(lockKey)
                    skipped += 1
                    continue

                receiptUser = (userId or "unknown")[-8:]
                receiptCycle = now.strftime("%Y%m")
                receipt = f"ren_{receiptUser}_{receiptCycle}_{int(time.time())}"

                try:
                    order = self.razorpayClient.order.create(
                        {
                            "amount": amount,
                            "currency": "INR",
                            "customer_id": customerId,
                            "receipt": receipt,
                            "payment_capture": True,
                            "notes": {
                                "type": "recurring_renewal",
                                "userId": userId,
                                "domainCount": str(domainCount),
                                "attemptId": attemptId,
                            },
                        }
                    )
                    orderId = order["id"]

                    self._updatePaymentAttemptStatus(
                        attemptId, "pending_provider_ack",
                        providerOrderId=orderId,
                    )

                    response = self.razorpayClient.payment.createRecurring(
                        {
                            "email": user.get("email", ""),
                            "contact": user.get("phoneNumber", "9999999999"),
                            "amount": amount,
                            "currency": "INR",
                            "order_id": orderId,
                            "token": tokenId,
                            "customer_id": customerId,
                            "recurring": True,
                            "description": f"Subscription renewal - {domainCount} domain(s)",
                            "notes": {
                                "type": "recurring_renewal",
                                "userId": userId,
                                "domainCount": str(domainCount),
                                "attemptId": attemptId,
                            },
                        }
                    )
                except Exception as providerError:
                    self._updatePaymentAttemptStatus(
                        attemptId, "failed",
                        failureReason=f"provider_call_failed: {providerError}",
                    )
                    self.redis.delete(lockKey)
                    raise providerError

                paymentId = response.get("razorpay_payment_id") or response.get("id")

                self._updatePaymentAttemptStatus(
                    attemptId, "pending_provider_ack",
                    providerPaymentId=paymentId,
                    providerOrderId=orderId,
                )

                self._auditLog(
                    userId,
                    "billing.renewal_queued",
                    status="QUEUED",
                    metadata={
                        "amount": amount,
                        "domainCount": domainCount,
                        "tokenId": tokenId,
                        "orderId": orderId,
                        "razorpayPaymentId": paymentId,
                        "attemptId": attemptId,
                    },
                )
                queued += 1
                logger.info(
                    f"Recurring charge queued for user {userId}, amount={amount}"
                )

            except Exception as e:
                logger.error(f"Failed to queue recurring charge for user {userId}: {e}")
                self._handleChargeError(
                    userId, error=e,
                    tokenId=user.get("razorpayTokenId", "") if "user" in dir() else "",
                    customerId=user.get("razorpayCustomerId", "") if "user" in dir() else "",
                )
                errors += 1

            time.sleep(0.5)

        logger.info(f"Charge execution complete: {queued} queued, {skipped} skipped, {errors} errors")
        return {"queued": queued, "skipped": skipped, "errors": errors}

    def _processPendingRemovals(self, user: dict) -> None:
        """
        Process pending domain removals at cycle boundary.

        Removes domains from subscribedExperts, updates domainCount,
        and clears pendingRemovals.

        Args:
            user (dict): The user record.
        """
        pendingRemovals = user.get("pendingRemovals") or []
        if not pendingRemovals:
            return
        experts = user.get("subscribedExperts") or ""
        currentExperts = [e.strip() for e in experts.split(",") if e.strip()]
        updatedExperts = [e for e in currentExperts if e not in pendingRemovals]
        self.client.table("Users").update(
            {
                "subscribedExperts": ", ".join(updatedExperts),
                "domainCount": len(updatedExperts),
                "pendingRemovals": [],
            }
        ).eq("userId", user["userId"]).execute()
        logger.info(
            f"Processed pending removals for user {user['userId']}: {pendingRemovals}"
        )

    def _handleChargeError(self, userId: str, error: Exception = None,
                           tokenId: str = "", customerId: str = "") -> None:
        """
        Log an API-level error when order creation or recurring payment fails.

        This handles SDK / network errors only. Actual payment failures
        (insufficient funds, card declined, etc.) arrive via the
        payment.failed webhook and are handled there.

        Args:
            userId (str): The user ID.
            error (Exception): The exception that caused the failure.
            tokenId (str): The Razorpay token ID used for the charge attempt.
            customerId (str): The Razorpay customer ID.
        """
        self._auditLog(
            userId,
            "billing.queue_error",
            status="ERROR",
            metadata={
                "reason": str(error) if error else "unknown",
                "errorType": type(error).__name__ if error else "unknown",
                "tokenId": tokenId,
                "customerId": customerId,
            },
        )
