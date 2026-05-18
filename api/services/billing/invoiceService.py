"""
invoiceService.py

Service layer for the annual renewal invoice lifecycle.

Provides idempotent helpers for:
    - Creating draft/upcoming renewal invoices (T-30).
    - Freezing final pricing and creating Razorpay payment artifacts (T-7).
    - Transitioning invoice and subscription states through the
      payment_pending → paid / past_due → suspended lifecycle.

All mutations are guarded by DB uniqueness constraints and Redis
advisory locks to ensure idempotent scheduler reruns.
"""

__version__ = "1.0.0"
__author__ = "Rohit Mishra"
__all__ = [
    "createUpcomingRenewalInvoice",
    "createPaymentArtifact",
    "transitionToPastDue",
    "transitionToSuspended",
]


from api.services.billing.billingEngine import computeInvoiceSnapshot
from api.services.subscriptions.subscriptionFieldUtils import (
    SUBSCRIPTION_BILLING_FIELDS_SELECT,
    subscriptionBillingState,
    subscriptionCustomerId,
    subscriptionDomainCount,
)
from supabase import create_client
from utils.logger import logger
from dateutil.relativedelta import relativedelta
import razorpay
import datetime
import redis
import os


_RECONCILIATION_BUFFER_MINUTES = 60


def _getSupabaseClient():
    """
    Create and return a Supabase client.

    Returns:
        Client: A Supabase client instance.
    """
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def _getRazorpayClient() -> razorpay.Client:
    """
    Create an authenticated Razorpay client.

    Returns:
        razorpay.Client: Razorpay client instance.
    """
    return razorpay.Client(
        auth=(
            os.environ.get("RAZORPAY_KEY_ID", ""),
            os.environ.get("RAZORPAY_KEY_SECRET", ""),
        )
    )


def _getRedisClient() -> redis.Redis:
    """
    Create a connected Redis client.

    Returns:
        redis.Redis: Redis client instance.
    """
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        password=os.environ.get("REDIS_PASSWORD", None),
        decode_responses=True,
    )


def _acquireAdvisoryLock(redisClient: redis.Redis, lockKey: str,
                         ttlSeconds: int = 300) -> bool:
    """
    Acquire a Redis advisory lock for idempotent scheduler operations.

    Args:
        redisClient: Redis client instance.
        lockKey: Unique lock key.
        ttlSeconds: Lock TTL in seconds.

    Returns:
        bool: True if lock acquired, False if already held.
    """
    return bool(redisClient.set(lockKey, "1", nx=True, ex=ttlSeconds))


def createUpcomingRenewalInvoice(subscription: dict, user: dict) -> dict | None:
    """
    Create or return an existing upcoming renewal invoice for an
    annual subscription approaching its renewal window (T-30).

    Uses the DB unique index idx_invoices_renewal_period_unique to
    prevent duplicate invoices for the same subscription period.

    The pricing snapshot is estimated at this stage and will be
    recomputed and frozen at T-7 before payment artifact creation.

    Args:
        subscription: Row from the subscriptions table.
        user: Row from the Users table (identity/profile fields).

    Returns:
        dict | None: The invoice row if created/found, None on error.
    """
    client = _getSupabaseClient()
    redisClient = _getRedisClient()
    subscriptionId = subscription["id"]
    userId = subscription["user_id"]
    periodEnd = subscription["current_period_end"]
    nextPeriodStart = periodEnd
    nextPeriodEnd = None

    try:
        from dateutil import parser as dtparser
        periodEndDt = dtparser.isoparse(periodEnd)
        if periodEndDt.tzinfo is None:
            periodEndDt = periodEndDt.replace(
                tzinfo=datetime.timezone.utc
            )
        nextPeriodEndDt = periodEndDt + relativedelta(years=1)
        nextPeriodEnd = nextPeriodEndDt.isoformat()
    except Exception:
        nextPeriodEnd = nextPeriodStart

    existing = (
        client.table("Invoices")
        .select("id, status, total_amount")
        .eq("subscription_id", subscriptionId)
        .eq("billing_reason", "renewal")
        .eq("period_start", nextPeriodStart)
        .eq("period_end", nextPeriodEnd)
        .limit(1)
        .execute()
        .data
    )
    if existing:
        logger.info(
            f"Renewal invoice already exists for subscription {subscriptionId}"
        )
        return existing[0]

    lockKey = f"invoice:renewal:{subscriptionId}:{nextPeriodStart}:{nextPeriodEnd}"
    if not _acquireAdvisoryLock(redisClient, lockKey, ttlSeconds=120):
        logger.info(
            f"Renewal invoice lock held for subscription {subscriptionId}, skipping"
        )
        return None

    domainCount = subscriptionDomainCount(subscription)
    if domainCount < 1:
        logger.warning(
            f"Subscription {subscriptionId} has 0 domains, skipping invoice creation"
        )
        return None

    try:
        snapshot = computeInvoiceSnapshot(
            billingMode="annual_prepaid",
            billingReason="renewal",
            domainCount=domainCount,
            customerState=subscriptionBillingState(subscription),
            periodStart=dtparser.isoparse(nextPeriodStart) if isinstance(nextPeriodStart, str) else nextPeriodStart,
            periodEnd=nextPeriodEndDt,
        )
    except Exception as e:
        logger.error(
            f"Failed to compute renewal snapshot for subscription {subscriptionId}: {e}"
        )
        return None

    dueDate = periodEndDt.isoformat()

    payload = {
        "userId": userId,
        "subscription_id": subscriptionId,
        "billing_reason": "renewal",
        "payment_flow": "razorpay_invoice",
        "requires_customer_auth": True,
        "period_start": snapshot.period_start,
        "period_end": snapshot.period_end,
        "amount_before_tax": snapshot.amount_before_tax,
        "tax_amount": snapshot.tax.tax_amount,
        "total_amount": snapshot.total_amount,
        "amount": snapshot.total_amount,
        "currency": snapshot.currency,
        "status": "upcoming",
        "due_date": dueDate,
        "tax_breakdown_json": snapshot.tax.to_dict(),
        "tax_rule_version": snapshot.tax.tax_rule_version,
        "place_of_supply_snapshot": snapshot.tax.place_of_supply_snapshot,
        "pricing_version": snapshot.pricing_version,
        "pricing_reference_snapshot_json": snapshot.pricing_reference_snapshot_json,
        "metadata_json": {
            "flow": "annual_renewal_scheduler_t30",
            "billingMode": "annual_prepaid",
            "estimate": True,
        },
    }

    try:
        result = client.table("Invoices").insert(payload).execute()
        if result.data:
            logger.info(
                f"Renewal invoice created for subscription {subscriptionId}, "
                f"invoiceId={result.data[0]['id']}"
            )
            return result.data[0]
    except Exception as e:
        errorStr = str(e).lower()
        if "duplicate" in errorStr or "unique" in errorStr or "23505" in errorStr:
            logger.info(
                f"Duplicate renewal invoice blocked by DB for subscription {subscriptionId}"
            )
            return (
                client.table("Invoices")
                .select("id, status, total_amount")
                .eq("subscription_id", subscriptionId)
                .eq("billing_reason", "renewal")
                .eq("period_start", nextPeriodStart)
                .eq("period_end", nextPeriodEnd)
                .limit(1)
                .execute()
                .data or [None]
            )[0]
        logger.error(
            f"Failed to insert renewal invoice for subscription {subscriptionId}: {e}"
        )
    return None


def createPaymentArtifact(invoice: dict, user: dict) -> dict | None:
    """
    Recompute final pricing, freeze the invoice snapshot, and create a
    Razorpay Invoice or Payment Link for the renewal invoice (T-7).

    If a payment artifact already exists on the invoice, this is a no-op.

    Args:
        invoice: The upcoming/payment_pending invoice row.
        user: User row (email, fullName, phoneNumber).

    Returns:
        dict | None: Updated invoice row with artifact IDs, or None on error.
    """
    client = _getSupabaseClient()
    razorpayClient = _getRazorpayClient()
    redisClient = _getRedisClient()
    invoiceId = invoice["id"]

    if invoice.get("razorpayInvoiceId") or invoice.get("razorpay_payment_link_id"):
        logger.info(
            f"Payment artifact already exists for invoice {invoiceId}, skipping"
        )
        return invoice

    lockKey = f"invoice:artifact:{invoiceId}"
    if not _acquireAdvisoryLock(redisClient, lockKey, ttlSeconds=180):
        logger.info(f"Artifact creation lock held for invoice {invoiceId}, skipping")
        return None

    subscriptionId = invoice.get("subscription_id")
    subscription = (
        client.table("subscriptions")
        .select(f"id, current_period_end, billing_mode, status, {SUBSCRIPTION_BILLING_FIELDS_SELECT}")
        .eq("id", subscriptionId)
        .limit(1)
        .execute()
        .data
    )
    if not subscription:
        logger.error(f"Subscription {subscriptionId} not found for invoice {invoiceId}")
        return None

    subscription = subscription[0]
    domainCount = subscriptionDomainCount(subscription)
    if domainCount < 1:
        logger.warning(f"User has 0 domains for invoice {invoiceId}, skipping artifact")
        return None

    try:
        from dateutil import parser as dtparser
        periodStartDt = dtparser.isoparse(invoice["period_start"])
        periodEndDt = dtparser.isoparse(invoice["period_end"])
        snapshot = computeInvoiceSnapshot(
            billingMode="annual_prepaid",
            billingReason="renewal",
            domainCount=domainCount,
            customerState=subscriptionBillingState(subscription),
            periodStart=periodStartDt,
            periodEnd=periodEndDt,
        )
    except Exception as e:
        logger.error(
            f"Failed to recompute snapshot for invoice {invoiceId}: {e}"
        )
        return None

    paymentFlow = "razorpay_invoice"
    razorpayInvoiceId = ""
    razorpayPaymentLinkId = ""
    shortUrl = ""
    commonNotes = {
        "invoiceId": invoiceId,
        "subscriptionId": subscriptionId,
        "userId": user.get("userId", ""),
        "billingReason": "renewal",
        "pricingVersion": snapshot.pricing_version,
    }
    now = datetime.datetime.now(datetime.timezone.utc)
    expireByDt = periodStartDt if periodStartDt > now else now + datetime.timedelta(days=7)

    try:
        rzpInvoice = razorpayClient.invoice.create({
            "type": "invoice",
	            "customer_id": subscriptionCustomerId(subscription),
            "line_items": [
                {
                    "name": f"Annual Renewal - {domainCount} domain(s)",
                    "amount": snapshot.total_amount,
                    "currency": "INR",
                    "quantity": 1,
                }
            ],
            "expire_by": int(expireByDt.timestamp()) if expireByDt else None,
            "sms_notify": 0,
            "email_notify": 0,
            "notes": commonNotes,
        })
        razorpayInvoiceId = rzpInvoice.get("id", "")
        shortUrl = rzpInvoice.get("short_url", "")
    except Exception as invoiceError:
        logger.warning(
            f"Razorpay invoice creation failed for invoice {invoiceId}, "
            f"trying payment link fallback: {invoiceError}"
        )
        try:
            paymentFlow = "razorpay_payment_link"
            rzpPaymentLink = razorpayClient.payment_link.create({
                "amount": snapshot.total_amount,
                "currency": "INR",
                "accept_partial": False,
                "description": f"Annual Renewal - {domainCount} domain(s)",
                "customer": {
                    "name": user.get("fullName", ""),
                    "email": user.get("email", ""),
                    "contact": user.get("phoneNumber", ""),
                },
                "expire_by": int(expireByDt.timestamp()) if expireByDt else None,
                "notify": {"sms": False, "email": False},
                "reference_id": str(invoiceId),
                "notes": commonNotes,
            })
            razorpayPaymentLinkId = rzpPaymentLink.get("id", "")
            shortUrl = rzpPaymentLink.get("short_url", "")
        except Exception as paymentLinkError:
            logger.error(
                f"Razorpay payment artifact creation failed for invoice {invoiceId}: "
                f"invoice_error={invoiceError}; payment_link_error={paymentLinkError}"
            )
            return None

    updatePayload = {
        "razorpayInvoiceId": razorpayInvoiceId,
        "razorpay_payment_link_id": razorpayPaymentLinkId,
        "shortUrl": shortUrl,
        "payment_flow": paymentFlow,
        "status": "payment_pending",
        "expires_at": expireByDt.isoformat() if expireByDt else None,
        "amount_before_tax": snapshot.amount_before_tax,
        "tax_amount": snapshot.tax.tax_amount,
        "total_amount": snapshot.total_amount,
        "amount": snapshot.total_amount,
        "tax_breakdown_json": snapshot.tax.to_dict(),
        "tax_rule_version": snapshot.tax.tax_rule_version,
        "place_of_supply_snapshot": snapshot.tax.place_of_supply_snapshot,
        "pricing_version": snapshot.pricing_version,
        "pricing_reference_snapshot_json": snapshot.pricing_reference_snapshot_json,
        "metadata_json": {
            "flow": "annual_renewal_scheduler_t7",
            "billingMode": "annual_prepaid",
            "estimate": False,
            "frozen": True,
        },
    }
    client.table("Invoices").update(updatePayload).eq("id", invoiceId).execute()

    logger.info(
        f"Payment artifact created for invoice {invoiceId}, "
        f"razorpayInvoiceId={razorpayInvoiceId}"
    )
    return {**invoice, **updatePayload}


def transitionToPastDue(subscription: dict, invoice: dict) -> bool:
    """
    Transition a subscription to past_due when its renewal invoice
    remains unpaid after the billing period has ended.

    Checks invoice status before transitioning to guard against
    race conditions with concurrent webhook-paid events.

    Args:
        subscription: Row from the subscriptions table.
        invoice: The unpaid renewal invoice row.

    Returns:
        bool: True if transition occurred, False if skipped/blocked.
    """
    client = _getSupabaseClient()
    invoiceId = invoice["id"]
    subscriptionId = subscription["id"]

    freshInvoice = (
        client.table("Invoices")
        .select("status")
        .eq("id", invoiceId)
        .limit(1)
        .execute()
        .data
    )
    if freshInvoice and freshInvoice[0].get("status", "").lower() in ("paid", "void"):
        logger.info(
            f"Invoice {invoiceId} already resolved, skipping past_due transition"
        )
        return False

    currentStatus = subscription.get("status", "")
    if currentStatus in ("past_due", "suspended", "cancelled", "expired"):
        logger.info(
            f"Subscription {subscriptionId} already in terminal/past_due state, skipping"
        )
        return False

    client.table("subscriptions").update({
        "status": "past_due",
    }).eq("id", subscriptionId).execute()

    logger.info(
        f"Subscription {subscriptionId} transitioned to past_due"
    )
    return True


def transitionToSuspended(subscription: dict, invoice: dict) -> bool:
    """
    Transition a past_due subscription to suspended after the
    reconciliation buffer window has elapsed.

    Performs a final invoice status check to handle payment arrival
    race conditions.

    Args:
        subscription: Row from the subscriptions table.
        invoice: The unpaid renewal invoice row.

    Returns:
        bool: True if suspension occurred, False if skipped/blocked.
    """
    client = _getSupabaseClient()
    invoiceId = invoice["id"]
    subscriptionId = subscription["id"]

    freshInvoice = (
        client.table("Invoices")
        .select("status")
        .eq("id", invoiceId)
        .limit(1)
        .execute()
        .data
    )
    if freshInvoice and freshInvoice[0].get("status", "").lower() in ("paid", "void"):
        logger.info(
            f"Invoice {invoiceId} paid during buffer window, cancelling suspension"
        )
        return False

    currentStatus = subscription.get("status", "")
    if currentStatus != "past_due":
        logger.info(
            f"Subscription {subscriptionId} not in past_due state ({currentStatus}), "
            f"skipping suspension"
        )
        return False

    client.table("subscriptions").update({
        "status": "suspended",
    }).eq("id", subscriptionId).execute()

    logger.info(
        f"Subscription {subscriptionId} suspended due to non-payment"
    )
    return True
