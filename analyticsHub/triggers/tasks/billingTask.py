"""
billingTask.py

Daily billing engine for the token-based recurring payment system.
Runs as a Celery Beat task at midnight UTC.

Queries users whose subscription expires within the next 48 hours and
fires Razorpay's create_recurring_payment API against their saved token.
Razorpay and the issuing bank handle the RBI-mandated 24-hour pre-debit
notification automatically. All post-payment state changes (expiry bump,
invoice creation, dunning) are handled by the payment.captured and
payment.failed webhook handlers in webhookService.
"""

__version__ = "2.0.0"
__author__ = "Rohit Mishra"
__all__ = ["DailyBillingTask"]

from supabase import create_client
from utils.logger import logger
import razorpay
import datetime
import redis
import time
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
    subscriptions approaching renewal. Does not update subscription state
    directly — that is the webhook handlers' responsibility.
    """

    _CHARGE_LOCK_TTL_SECONDS = 72 * 60 * 60
    _PRICE_CACHE_KEY = "billing:base_price_amount"
    _PRICE_CACHE_TTL = 24 * 60 * 60
    _PRICE_FETCH_RETRIES = 3

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
            dict: Counts of queued charges and API-level errors.
        """
        logger.info("Daily billing task started")
        results = self._chargeExecution()
        logger.info(
            f"Daily billing task completed: {results['queued']} queued, "
            f"{results['errors']} errors"
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
                self.redis.set(self._PRICE_CACHE_KEY, str(amount), ex=self._PRICE_CACHE_TTL)
                return amount
            except Exception as e:
                lastError = e
                logger.warning(f"Plan fetch attempt {attempt}/{self._PRICE_FETCH_RETRIES} failed: {e}")
                if attempt < self._PRICE_FETCH_RETRIES:
                    time.sleep(2 ** attempt)

        cached = self.redis.get(self._PRICE_CACHE_KEY)
        if cached:
            logger.warning(f"Using cached base price: {cached}")
            return int(cached)

        raise RuntimeError(f"Cannot fetch base price and no cached value available: {lastError}")

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
            self.client.table("SubscriptionLog").insert({
                "userId": userId,
                "eventType": eventType,
                "status": status,
                "metadata": metadata if metadata else None,
            }).execute()
        except Exception as e:
            logger.error(f"SubscriptionLog insert failed for user {userId}, event {eventType}: {e}")

    def _chargeExecution(self) -> dict:
        """
        Queue recurring charges for users whose cycle ends within 48 hours.

        Fires create_recurring_payment against each user's saved Razorpay
        token. The actual payment settlement, DB updates, and dunning are
        handled asynchronously by the payment.captured / payment.failed
        webhook handlers.

        Returns:
            dict: Counts of queued charges and API-level errors.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        chargeWindowEnd = now + datetime.timedelta(hours=48)

        users = self.client.table("Users") \
            .select("userId, email, fullName, domainCount, razorpayTokenId, "
                    "razorpayCustomerId, subscriptionExpiry, subscriptionAnchorDay, "
                    "recurringFailures, pendingRemovals, subscribedExperts, subscriptionStatus, "
                    "phoneNumber") \
            .eq("subscriptionStatus", "ACTIVE") \
            .gte("subscriptionExpiry", now.isoformat()) \
            .lte("subscriptionExpiry", chargeWindowEnd.isoformat()) \
            .not_.is_("razorpayTokenId", "null") \
            .execute().data

        if not users:
            logger.info("No users due for charge queuing")
            return {"queued": 0, "errors": 0}

        basePriceAmount = self._getBasePriceAmount()
        queued = 0
        errors = 0

        for user in users:
            userId = user["userId"]
            try:
                if (user.get("recurringFailures") or 0) >= 3:
                    logger.info(f"User {userId} has reached max recurring failures, skipping charge")
                    continue

                self._processPendingRemovals(user)

                refreshed = self.client.table("Users") \
                    .select("domainCount, subscribedExperts") \
                    .eq("userId", userId).execute().data[0]
                domainCount = refreshed.get("domainCount") or 0

                if domainCount < 1:
                    logger.warning(f"User {userId} has 0 domains after removal processing, skipping charge")
                    continue

                amount = domainCount * basePriceAmount
                tokenId = user["razorpayTokenId"]
                customerId = user["razorpayCustomerId"]

                cycleKey = now.strftime("%Y-%m")
                lockKey = f"billing:charge:{userId}:{cycleKey}"
                acquired = self.redis.set(
                    lockKey, "1", nx=True, ex=self._CHARGE_LOCK_TTL_SECONDS
                )
                if not acquired:
                    logger.info(f"User {userId} already has a charge lock for cycle {cycleKey}, skipping")
                    continue

                try:
                    response = self.razorpayClient.payment.createRecurring({
                        "email": user.get("email", ""),
                        "contact": user.get("phoneNumber", "9999999999"),
                        "type": "recurring",
                        "amount": amount,
                        "currency": "INR",
                        "token": tokenId,
                        "customer_id": customerId,
                        "capture": 1,
                        "description": f"Subscription renewal - {domainCount} domain(s)",
                        "notes": {
                            "type": "recurring_renewal",
                            "userId": userId,
                            "domainCount": str(domainCount),
                        },
                    })
                except Exception:
                    self.redis.delete(lockKey)
                    raise

                paymentId = response.get("razorpay_payment_id") or response.get("id")

                self._auditLog(
                    userId, "billing.renewal_queued",
                    status="QUEUED",
                    metadata={
                        "amount": amount,
                        "domainCount": domainCount,
                        "tokenId": tokenId,
                        "razorpayPaymentId": paymentId,
                    }
                )
                queued += 1
                logger.info(f"Recurring charge queued for user {userId}, amount={amount}")

            except Exception as e:
                logger.error(f"Failed to queue recurring charge for user {userId}: {e}")
                self._handleChargeError(userId)
                errors += 1

            time.sleep(0.5)

        logger.info(f"Charge execution complete: {queued} queued, {errors} errors")
        return {"queued": queued, "errors": errors}

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
        self.client.table("Users").update({
            "subscribedExperts": ", ".join(updatedExperts),
            "domainCount": len(updatedExperts),
            "pendingRemovals": [],
        }).eq("userId", user["userId"]).execute()
        logger.info(f"Processed pending removals for user {user['userId']}: {pendingRemovals}")

    def _handleChargeError(self, userId: str) -> None:
        """
        Log an API-level error when the create_recurring_payment call fails.

        This handles SDK / network errors only. Actual payment failures
        (insufficient funds, card declined, etc.) arrive via the
        payment.failed webhook and are handled there.

        Args:
            userId (str): The user ID.
        """
        self._auditLog(
            userId, "billing.queue_error",
            status="ERROR",
            metadata={"reason": "create_recurring_payment API call failed"}
        )
